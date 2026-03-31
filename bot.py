import telebot
import anthropic
import base64
import os

# Все настройки через переменные окружения Railway
BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_ID', '0'))

bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()

def get_client():
    key = os.environ.get('ANTHROPIC_KEY', '')
    if not key:
        raise ValueError("API ключ не установлен. Задайте ANTHROPIC_KEY в переменных Railway.")
    return anthropic.Anthropic(api_key=key)

# ─── Команды ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 Привет! Отправь мне фото или PDF-документ, и я распознаю из него текст.\n\n"
        "Поддерживаемые форматы: PDF, JPG, PNG\n"
        "⚠️ Максимальный размер файла: 20MB\n\n"
        "/model — текущая модель\n"
        "/status — статус бота (только для администратора)"
    )

@bot.message_handler(commands=['model'])
def show_model(message):
    bot.reply_to(message, "🤖 Модель: `claude-sonnet-4-20250514`", parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def show_status(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Нет доступа.")
        return
    key = os.environ.get('ANTHROPIC_KEY', '')
    masked = key[:12] + '...' + key[-4:] if len(key) > 16 else '❌ не задан'
    bot.reply_to(message, f"✅ Бот работает\n🔑 Ключ: `{masked}`", parse_mode='Markdown')

# ─── Обработка файлов ─────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

PROMPT = """Распознай и выведи весь текст с этого файла.
Сначала проанализируй визуальную структуру документа.
Если текст разделён на несколько колонок — добавь в самом начале строку:
«⚠️ ВНИМАНИЕ: Текст в оригинале разделён на колонки»
Затем выведи текст в правильном порядке чтения (колонка за колонкой сверху вниз).
Выводи только текст, без Markdown, без звёздочек и прочего визуального мусора."""

def send_long(chat_id, text, max_len=4096):
    for i in range(0, len(text), max_len):
        bot.send_message(chat_id, text[i:i + max_len])

def check_size(file_size, chat_id):
    if file_size and file_size > MAX_FILE_SIZE:
        mb = file_size / (1024 * 1024)
        bot.send_message(
            chat_id,
            f"❌ Файл слишком большой: {mb:.1f}MB\n"
            f"Максимум: 20MB\n\n"
            f"💡 Сожмите PDF на ilovepdf.com"
        )
        return False
    return True

@bot.message_handler(content_types=['photo', 'document'])
def handle_file(message):
    try:
        if message.content_type == 'photo':
            photo = message.photo[-1]
            if not check_size(photo.file_size, message.chat.id): return
            file_info = bot.get_file(photo.file_id)
            mime_type = 'image/jpeg'
        else:
            doc = message.document
            ext = os.path.splitext(doc.file_name)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                bot.send_message(message.chat.id, f"❌ Формат «{ext}» не поддерживается.\nОтправьте PDF, JPG или PNG.")
                return
            if not check_size(doc.file_size, message.chat.id): return
            file_info = bot.get_file(doc.file_id)
            mime_type = (
                'application/pdf' if ext == '.pdf'
                else 'image/png'  if ext == '.png'
                else 'image/jpeg'
            )

        bot.send_message(message.chat.id, "⏳ Нейросеть изучает документ, подождите...")

        downloaded = bot.download_file(file_info.file_path)
        file_b64 = base64.standard_b64encode(downloaded).decode('utf-8')

        content = [
            {
                "type": "document" if mime_type == 'application/pdf' else "image",
                "source": {"type": "base64", "media_type": mime_type, "data": file_b64}
            },
            {"type": "text", "text": PROMPT}
        ]

        client = get_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": content}]
        )

        result = response.content[0].text.strip()
        if not result:
            bot.send_message(message.chat.id, "⚠️ Не удалось распознать текст — документ пустой или нечитаемый.")
            return

        send_long(message.chat.id, result)

    except ValueError as e:
        bot.send_message(message.chat.id, f"⚠️ {e}")
    except anthropic.AuthenticationError:
        bot.send_message(message.chat.id, "❌ Неверный API ключ. Обновите ANTHROPIC_KEY в Railway.")
    except anthropic.RateLimitError:
        bot.send_message(message.chat.id, "❌ Превышен лимит запросов. Попробуйте через минуту.")
    except anthropic.BadRequestError as e:
        if 'too large' in str(e).lower():
            bot.send_message(message.chat.id, "❌ Файл слишком большой для обработки.")
        else:
            bot.send_message(message.chat.id, f"❌ Ошибка запроса: {e}")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Неожиданная ошибка: {e}")

print("Бот запущен и готов к работе!")
bot.polling(none_stop=True, interval=1, timeout=30)
