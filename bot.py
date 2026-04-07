import telebot
import anthropic
import base64
import os
import io

BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))

bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()

def get_client():
    key = os.environ.get('ANTHROPIC_KEY', '')
    if not key:
        raise ValueError("API ключ не установлен.")
    return anthropic.Anthropic(api_key=key)

# ─── Режимы распознавания ─────────────────────────────────────────────────────

MODES = {
    'auto': {
        'name': '🔍 Авто',
        'system': None,
        'prompt': "Распознай текст на фото."
    },
    'handwritten': {
        'name': '✍️ Рукописный',
        'system': None,
        'prompt': "Распознай рукописный текст на фото. Выведи только текст."
    },
    'printed': {
        'name': '📄 Печатный',
        'system': None,
        'prompt': "Распознай печатный текст на фото. Если текст в колонках — сначала левая, потом правая. Выведи только текст."
    },
    'mixed': {
        'name': '📝 Смешанный',
        'system': None,
        'prompt': "Распознай весь текст на фото — и печатный и рукописный. Выведи только текст в порядке чтения."
    }
}

# Хранилище режимов пользователей
user_modes = {}
user_formats = {}

# ─── Команды ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 Привет! Отправь мне фото или PDF-документ.\n\n"
        "Команды:\n"
        "/mode — режим распознавания (авто/рукописный/печатный)\n"
        "/format — формат ответа (текст/TXT файл)\n"
        "/model — текущая модель\n"
        "/status — статус (только админ)\n\n"
        "⚠️ Максимальный размер файла: 20MB"
    )

@bot.message_handler(commands=['model'])
def show_model(message):
    bot.reply_to(message, "🤖 Модель: `claude-sonnet-4-20250514`", parse_mode='Markdown')

@bot.message_handler(commands=['mode'])
def show_mode(message):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("🔍 Авто", callback_data='mode_auto'),
        telebot.types.InlineKeyboardButton("✍️ Рукописный", callback_data='mode_handwritten'),
    )
    markup.row(
        telebot.types.InlineKeyboardButton("📄 Печатный", callback_data='mode_printed'),
        telebot.types.InlineKeyboardButton("📝 Смешанный", callback_data='mode_mixed'),
    )
    current = user_modes.get(message.from_user.id, 'auto')
    bot.reply_to(message, f"Текущий режим: {MODES[current]['name']}\nВыберите режим:", reply_markup=markup)

@bot.message_handler(commands=['format'])
def show_format(message):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("💬 Текст", callback_data='fmt_text'),
        telebot.types.InlineKeyboardButton("📝 TXT файл", callback_data='fmt_txt'),
    )
    bot.reply_to(message, "Выберите формат ответа:", reply_markup=markup)

@bot.message_handler(commands=['status'])
def show_status(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Нет доступа.")
        return
    key = os.environ.get('ANTHROPIC_KEY', '')
    masked = key[:12] + '...' + key[-4:] if len(key) > 16 else '❌ не задан'
    bot.reply_to(message, f"✅ Бот работает\n🔑 Ключ: `{masked}`", parse_mode='Markdown')

@bot.callback_query_handler(func=lambda c: c.data.startswith('mode_'))
def handle_mode(call):
    mode = call.data.replace('mode_', '')
    user_modes[call.from_user.id] = mode
    bot.answer_callback_query(call.id, f"Режим: {MODES[mode]['name']}")
    bot.edit_message_text(f"✅ Режим: {MODES[mode]['name']}", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('fmt_'))
def handle_format(call):
    fmt = call.data.replace('fmt_', '')
    user_formats[call.from_user.id] = fmt
    names = {'text': '💬 Текст', 'txt': '📝 TXT файл'}
    bot.answer_callback_query(call.id, f"Формат: {names[fmt]}")
    bot.edit_message_text(f"✅ Формат: {names[fmt]}", call.message.chat.id, call.message.message_id)

# ─── Обработка файлов ─────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_FILE_SIZE = 20 * 1024 * 1024

def send_long(chat_id, text, max_len=4096):
    for i in range(0, len(text), max_len):
        bot.send_message(chat_id, text[i:i + max_len])

def check_size(file_size, chat_id):
    if file_size and file_size > MAX_FILE_SIZE:
        mb = file_size / (1024 * 1024)
        bot.send_message(chat_id, f"❌ Файл слишком большой: {mb:.1f}MB\nМаксимум: 20MB\n\n💡 Сожмите PDF на ilovepdf.com")
        return False
    return True

def send_result(message, text, orig_filename):
    fmt = user_formats.get(message.from_user.id, 'text')
    base_name = os.path.splitext(orig_filename)[0] if orig_filename else 'result'

    if fmt == 'txt':
        buf = io.BytesIO(text.encode('utf-8'))
        bot.send_document(message.chat.id, buf, visible_file_name=f"{base_name}_text.txt")
    else:
        send_long(message.chat.id, text)

@bot.message_handler(content_types=['photo', 'document'])
def handle_file(message):
    orig_filename = 'result'
    try:
        if message.content_type == 'photo':
            photo = message.photo[-1]
            if not check_size(photo.file_size, message.chat.id): return
            file_info = bot.get_file(photo.file_id)
            mime_type = 'image/jpeg'
        else:
            doc = message.document
            orig_filename = doc.file_name
            ext = os.path.splitext(doc.file_name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                bot.send_message(message.chat.id, f"❌ Формат «{ext}» не поддерживается.\nОтправьте PDF, JPG или PNG.")
                return
            if not check_size(doc.file_size, message.chat.id): return
            file_info = bot.get_file(doc.file_id)
            mime_type = (
                'application/pdf' if ext == '.pdf'
                else 'image/png' if ext == '.png'
                else 'image/jpeg'
            )

        # Получаем режим пользователя
        mode = user_modes.get(message.from_user.id, 'auto')
        mode_info = MODES[mode]

        bot.send_message(message.chat.id, f"⏳ Распознаю текст ({mode_info['name']})...")

        downloaded = bot.download_file(file_info.file_path)
        file_b64 = base64.standard_b64encode(downloaded).decode('utf-8')

        content = [
            {
                "type": "document" if mime_type == 'application/pdf' else "image",
                "source": {"type": "base64", "media_type": mime_type, "data": file_b64}
            },
            {"type": "text", "text": mode_info['prompt']}
        ]

        client = get_client()
        create_kwargs = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}]
        }
        if mode_info['system']:
            create_kwargs["system"] = mode_info['system']
        response = client.messages.create(**create_kwargs)

        result = response.content[0].text.strip()
        if not result:
            bot.send_message(message.chat.id, "⚠️ Не удалось распознать текст.")
            return

        send_result(message, result, orig_filename)

    except ValueError as e:
        bot.send_message(message.chat.id, f"⚠️ {e}")
    except anthropic.AuthenticationError:
        bot.send_message(message.chat.id, "❌ Неверный API ключ.")
    except anthropic.RateLimitError:
        bot.send_message(message.chat.id, "❌ Превышен лимит запросов. Попробуйте через минуту.")
    except anthropic.BadRequestError as e:
        if 'too large' in str(e).lower():
            bot.send_message(message.chat.id, "❌ Файл слишком большой.")
        else:
            bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

print("Бот запущен!")
bot.polling(none_stop=True, interval=1, timeout=30)
