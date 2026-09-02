import telebot
import base64
import os
import io
import json
import re
import urllib.request
import urllib.error
import urllib.parse

BOT_TOKEN  = os.environ['BOT_TOKEN']
ADMIN_ID   = int(os.environ.get('ADMIN_ID', '0'))
GEMINI_KEY = os.environ.get('GEMINI_KEY', '').strip()
GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta'

SKIP_SUBSTR = (
    'embedding', 'imagen', 'veo', 'tts', 'live', 'transcribe', 'image',
    'robotics', 'gemma', 'computer', 'aqa', 'learnlm', 'omni', 'audio',
    'cyber', 'code-assist',
)

FALLBACK_MODELS = [
    'gemini-3.8-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-3.5-flash',
    'gemini-3.5-flash-lite',
    'gemini-3.1-pro-preview',
    'gemini-3-flash-preview',
    'gemini-2.5-flash',
    'gemini-2.5-pro',
]

bot = telebot.TeleBot(BOT_TOKEN)
bot.remove_webhook()
print(f'GEMINI_KEY set: {bool(GEMINI_KEY)}, length: {len(GEMINI_KEY)}')

def gemini_url(path, extra=None):
    params = {'key': GEMINI_KEY}
    if extra:
        params.update(extra)
    return GEMINI_API + '/' + path + '?' + urllib.parse.urlencode(params)

def model_sort_key(name):
    m = re.match(r'gemini-(\d+)(?:\.(\d+))?(?:\.(\d+))?', name)
    major = int(m.group(1)) if m else 0
    minor = int(m.group(2)) if m and m.group(2) else 0
    patch = int(m.group(3)) if m and m.group(3) else 0
    if 'flash-lite' in name:
        family = 1
    elif 'flash' in name:
        family = 3
    elif 'pro' in name:
        family = 2
    else:
        family = 0
    stable = 0 if ('preview' in name or '-exp' in name) else 1
    undated = 0 if re.search(r'-\d{2}-\d{2}', name) else 1
    return (major, minor, patch, family, stable, undated, name)

def is_ocr_model(name, methods):
    if methods and 'generateContent' not in methods:
        return False
    n = name.lower()
    if not n.startswith('gemini-'):
        return False
    if any(s in n for s in SKIP_SUBSTR):
        return False
    return 'flash' in n or 'pro' in n

def fetch_available_models():
    names = []
    page_token = None
    try:
        while True:
            extra = {'pageSize': '100'}
            if page_token:
                extra['pageToken'] = page_token
            req = urllib.request.Request(gemini_url('models', extra), method='GET')
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            for item in data.get('models', []):
                raw = item.get('name', '')
                name = raw.split('/')[-1]
                methods = item.get('supportedGenerationMethods', [])
                if is_ocr_model(name, methods):
                    names.append(name)
            page_token = data.get('nextPageToken')
            if not page_token:
                break
    except Exception as e:
        print('Не удалось получить список моделей Gemini:', e)
        return list(FALLBACK_MODELS)

    cleaned = []
    seen = set()
    for name in names:
        base = re.sub(r'-00\d$', '', name)
        if base in seen:
            continue
        seen.add(base)
        cleaned.append(base)
    cleaned.sort(key=model_sort_key, reverse=True)
    return cleaned or list(FALLBACK_MODELS)

def pick_latest_flash(models):
    for name in models:
        if 'flash' in name and 'lite' not in name:
            return name
    return models[0] if models else FALLBACK_MODELS[0]

AVAILABLE_MODELS = fetch_available_models()
DEFAULT_MODEL = pick_latest_flash(AVAILABLE_MODELS)
print('Доступные модели:', ', '.join(AVAILABLE_MODELS[:12]))
print('Автомодель:', DEFAULT_MODEL)

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
    global AVAILABLE_MODELS, DEFAULT_MODEL
    AVAILABLE_MODELS = fetch_available_models()
    DEFAULT_MODEL = pick_latest_flash(AVAILABLE_MODELS)
    uid = message.from_user.id
    current = user_models.get(uid, 'auto')
    markup = telebot.types.InlineKeyboardMarkup()
    auto_label = ("✅ " if current == 'auto' else "") + "Авто (" + DEFAULT_MODEL + ")"
    markup.add(telebot.types.InlineKeyboardButton(auto_label, callback_data="mdl_auto"))
    for name in AVAILABLE_MODELS[:16]:
        label = ("✅ " if name == current else "") + name
        markup.add(telebot.types.InlineKeyboardButton(label, callback_data="mdl_" + name))
    shown = DEFAULT_MODEL if current == 'auto' else current
    bot.reply_to(message, "Сейчас: " + shown + "\nАвтоподключение к самой свежей Flash.\nВыберите:", reply_markup=markup)

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
    key = GEMINI_KEY
    masked = key[:8] + '...' + key[-4:] if len(key) > 12 else '❌ не задан'
    bot.reply_to(
        message,
        "✅ Бот работает на Gemini\nКлюч: " + masked +
        "\nАвтомодель: " + DEFAULT_MODEL +
        "\nМоделей: " + str(len(AVAILABLE_MODELS))
    )

def resolve_model(uid):
    name = user_models.get(uid, 'auto')
    if name == 'auto' or name not in AVAILABLE_MODELS:
        return DEFAULT_MODEL
    return name

@bot.callback_query_handler(func=lambda c: c.data.startswith('mdl_'))
def handle_model(call):
    name = call.data.replace('mdl_', '', 1)
    if name != 'auto' and name not in AVAILABLE_MODELS:
        bot.answer_callback_query(call.id, "Модель недоступна")
        return
    user_models[call.from_user.id] = name
    shown = DEFAULT_MODEL if name == 'auto' else name
    label = "Авто (" + shown + ")" if name == 'auto' else shown
    bot.answer_callback_query(call.id, "Модель: " + label)
    bot.edit_message_text("✅ Модель: " + label, call.message.chat.id, call.message.message_id)

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
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": file_b64}},
            {"text": prompt}
        ]}],
        "generationConfig": {"maxOutputTokens": 8192}
    }

    query = urllib.parse.urlencode({'key': GEMINI_KEY})
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model_id
        + ":generateContent?"
        + query
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise Exception("HTTP " + str(e.code) + ": " + e.read().decode('utf-8')[:200])

    try:
        parts = result['candidates'][0]['content']['parts']
        text = ''.join(p.get('text', '') for p in parts).strip()
        return text
    except (KeyError, IndexError):
        candidate = result.get('candidates', [{}])[0]
        finish_reason = candidate.get('finishReason', 'UNKNOWN')
        raise Exception("Не удалось распознать (причина: " + str(finish_reason) + ")")

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
        model_id = resolve_model(message.from_user.id)

        bot.send_message(message.chat.id, "⏳ Распознаю текст (" + MODES[mode]['name'] + " / " + model_id + ")...")

        file_data = bot.download_file(file_info.file_path)
        result = recognize(file_data, mime_type, mode, model_id)

        if not result:
            bot.send_message(message.chat.id, "⚠️ Не удалось распознать текст.")
            return

        send_result(message, result, orig_filename)

    except Exception as e:
        bot.send_message(message.chat.id, "❌ Ошибка: " + str(e))

print("Бот запущен на Gemini!")
bot.polling(none_stop=True, interval=1, timeout=30)
