import telebot
import anthropic
import base64
import os
import io

BOT_TOKEN  = os.environ['BOT_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_ID', '0'))

bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()

def get_client():
    key = os.environ.get('ANTHROPIC_KEY', '')
    if not key:
        raise ValueError("API ключ не установлен.")
    return anthropic.Anthropic(api_key=key)

MODES = {
    'auto': {
        'name': '🔍 Авто',
        'prompt': 'Распознай текст на фото. Выводи только сам текст без комментариев и пояснений.'
    },
    'handwritten': {
        'name': '✍️ Рукописный',
        'prompt': 'Распознай рукописный текст на фото. Если слово неразборчиво — напиши [?]. Выводи только текст без комментариев.'
    },
    'printed': {
        'name': '📄 Печатный',
        'prompt': 'Распознай печатный текст на фото. Если текст в колонках — сначала левая, потом правая. Выводи только текст без комментариев.'
    },
    'mixed': {
        'name': '📝 Смешанный',
        'prompt': 'Распознай весь текст на фото — и печатный и рукописный. Выводи только текст в порядке чтения без комментариев.'
    }
}

user_modes   = {}
user_formats = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
        "👋 Привет! Отправь мне фото или PDF.\n\n"
        "/mode — режим распознавания\n"
        "/format — формат ответа\n"
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
        telebot.types.InlineKeyboardButton("🔍 Авто",       callback_data='mode_auto'),
        telebot.types.InlineKeyboardButton("✍️ Рукописный", callback_data='mode_handwritten'),
    )
    markup.row(
        telebot.types.InlineKeyboardButton("📄 Печатный",   callback_data='mode_printed'),
        telebot.types.InlineKeyboardButton("📝 Смешанный",  callback_data='mode_mixed'),
    )
    current = user_modes.get(message.from_user.id, 'auto')
    bot.reply_to(message, f"Текущий режим: {MODES[current]['name']}\nВыберите режим:", reply_markup=markup)

@bot.message_handler(commands=['format'])
def show_format(message):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("💬 Текст",    callback_data='fmt_text'),
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

SUPPORTED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_FILE_SIZE = 20 * 1024 * 1024

def send_long(chat_id, text, max_len=4096):
    for i in range(0, len(text), max_len):
        bot.send_message(chat_id, text[i:i + max_len])

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
            if photo.file_size and photo.file_size > MAX_FILE_SIZE:
                bot.send_message(message.chat.id, "❌ Файл слишком большой. Максимум: 20MB")
                return
            file_info = bot.get_file(photo.file_id)
            mime_type = 'image/jpeg'
        else:
            doc = message.document
            orig_filename = doc.file_name
            ext = os.path.splitext(doc.file_name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                bot.send_message(message.chat.id, f"❌ Формат «{ext}» не поддерживается.")
                return
            if doc.file_size and doc.file_size > MAX_FILE_SIZE:
                bot.send_message(message.chat.id, "❌ Файл слишком большой. Максимум: 20MB")
                return
            file_info = bot.get_file(doc.file_id)
            mime_type = (
                'application/pdf' if ext == '.pdf'
                else 'image/png'  if ext == '.png'
                else 'image/jpeg'
            )

        mode = user_modes.get(message.from_user.id, 'auto')
        bot.send_message(message.chat.id, f"⏳ Распознаю текст ({MODES[mode]['name']})...")

        file_data = bot.download_file(file_info.file_path)
        file_b64 = base64.standard_b64encode(file_data).decode('utf-8')

        content_block = {
            "type": "document" if mime_type == 'application/pdf' else "image",
            "source": {"type": "base64", "media_type": mime_type, "data": file_b64}
        }

        client = get_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": [
                content_block,
                {"type": "text", "text": MODES[mode]['prompt']}
            ]}]
        )

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
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

print("Бот запущен на Claude!")
bot.polling(none_stop=True, interval=1, timeout=30)
