import telebot
import base64
import os
import io
import json
import urllib.request

BOT_TOKEN      = os.environ['BOT_TOKEN']
ADMIN_ID       = int(os.environ.get('ADMIN_ID', '0'))
OPENROUTER_KEY = os.environ.get('OPENROUTER_KEY', '').strip()

MODELS = {
    'gemini-3.5-flash':  'google/gemini-3.5-flash',
    'gemini-3.1-pro':    'google/gemini-3.1-pro-preview',
    'gemini-3-pro':      'google/gemini-3-pro-preview',
    'gemini-3-flash':    'google/gemini-3-flash-preview',
    'gemini-2.5-pro':    'google/gemini-2.5-pro-preview',
    'gemini-2.5-flash':  'google/gemini-2.5-flash-preview-04-17',
    'gemini-2.0-flash':  'google/gemini-2.0-flash-001',
}
DEFAULT_MODEL = 'gemini-3.5-flash'

bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()
print(f'OPENROUTER_KEY set: {bool(OPENROUTER_KEY)}, length: {len(OPENROUTER_KEY)}')

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
user_models  = {}

# ─── Команды ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message,
        "👋 Привет! Отправь мне фото или PDF.\n\n"
        "/model — выбор модели\n"
        "/mode — режим распознавания\n"
        "/format — формат ответа\n"
        "/status — статус (только админ)\n\n"
        "⚠️ Максимальный размер файла: 20MB"
    )

@bot.message_handler(commands=['model'])
def show_model(message):
    uid = message.from_user.id
    current = user_models.get(uid, DEFAULT_MODEL)
    markup = telebot.types.InlineKeyboardMarkup()
    for name in MODELS:
        label = ("✅ " if name == current else "") + name
        markup.add(telebot.types.InlineKeyboardButton(label, callback_data="mdl_" + name))
    bot.reply_to(message, "Текущая модель: " + current + "\nВыберите:", reply_markup=markup)

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
    bot.reply_to(message, "Текущий режим: " + MODES[current]['name'] + "\nВыберите:", reply_markup=markup)

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
    key = OPENROUTER_KEY
    masked = key[:12] + '...' + key[-4:] if len(key) > 16 else '❌ не задан'
    bot.reply_to(message, "✅ Бот работает\nКлюч: " + masked)

@bot.callback_query_handler(func=lambda c: c.data.startswith('mdl_'))
def handle_model(call):
    name = call.data.replace('mdl_', '')
    user_models[call.from_user.id] = name
    bot.answer_callback_query(call.id, "Модель: " + name)
    bot.edit_message_text("✅ Модель: " + name, call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('mode_'))
def handle_mode(call):
    mode = call.data.replace('mode_', '')
    user_modes[call.from_user.id] = mode
    bot.answer_callback_query(call.id, "Режим: " + MODES[mode]['name'])
    bot.edit_message_text("✅ Режим: " + MODES[mode]['name'], call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('fmt_'))
def handle_format(call):
    fmt = call.data.replace('fmt_', '')
    user_formats[call.from_user.id] = fmt
    names = {'text': '💬 Текст', 'txt': '📝 TXT файл'}
    bot.answer_callback_query(call.id, "Формат: " + names[fmt])
    bot.edit_message_text("✅ Формат: " + names[fmt], call.message.chat.id, call.message.message_id)

# ─── Обработка файлов ─────────────────────────────────────────────────────────

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
        bot.send_document(message.chat.id, buf, visible_file_name=base_name + '_text.txt')
    else:
        send_long(message.chat.id, text)

def recognize(file_data, mime_type, mode, model_id):
    file_b64 = base64.standard_b64encode(file_data).decode('utf-8')
    prompt = MODES[mode]['prompt']

    body = {
        "model": model_id,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:" + mime_type + ";base64," + file_b64}
                },
                {"type": "text", "text": prompt}
            ]
        }]
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + OPENROUTER_KEY,
            'HTTP-Referer': 'https://github.com/Nikita34196/ocr-bot',
            'X-Title': 'OCR Bot'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content'].strip()
    except urllib.error.HTTPError as e:
        raise Exception("HTTP " + str(e.code) + ": " + e.read().decode('utf-8')[:200])

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
                bot.send_message(message.chat.id, "❌ Формат не поддерживается.")
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
        model_name = user_models.get(message.from_user.id, DEFAULT_MODEL)
        model_id = MODELS.get(model_name, MODELS[DEFAULT_MODEL])

        bot.send_message(message.chat.id, "⏳ Распознаю текст (" + MODES[mode]['name'] + " / " + model_name + ")...")

        file_data = bot.download_file(file_info.file_path)
        result = recognize(file_data, mime_type, mode, model_id)

        if not result:
            bot.send_message(message.chat.id, "⚠️ Не удалось распознать текст.")
            return

        send_result(message, result, orig_filename)

    except Exception as e:
        bot.send_message(message.chat.id, "❌ Ошибка: " + str(e))

print("Бот запущен на OpenRouter!")
bot.polling(none_stop=True, interval=1, timeout=30)
