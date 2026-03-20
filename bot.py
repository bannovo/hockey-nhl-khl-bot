import os
import time
import logging
import datetime
import re

import pytz
import telebot
import requests

from telebot import apihelper
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
HTTP_PROXY = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
HTTPS_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

if TELEGRAM_API_BASE.endswith("/"):
    TELEGRAM_API_BASE = TELEGRAM_API_BASE[:-1]

apihelper.API_URL = TELEGRAM_API_BASE + "/bot{0}/{1}"

if HTTP_PROXY or HTTPS_PROXY:
    proxy_url = HTTPS_PROXY or HTTP_PROXY
    apihelper.proxy = {
        "http": HTTP_PROXY or proxy_url,
        "https": HTTPS_PROXY or proxy_url,
    }
    logger.info(f"Для telebot настроен proxy: {apihelper.proxy}")
else:
    logger.info("Proxy для telebot не задан.")

bot = telebot.TeleBot(BOT_TOKEN)

RAW_HTTP_SESSION = requests.Session()
if HTTP_PROXY or HTTPS_PROXY:
    RAW_HTTP_SESSION.proxies.update({
        "http": HTTP_PROXY or HTTPS_PROXY,
        "https": HTTPS_PROXY or HTTP_PROXY,
    })

AUTO_SEND_CHAT_IDS = [188181889]

KHL_URL = "https://www.flashscorekz.com/hockey/russia/khl/results/"
KHL_FIXTURES_URL = "https://www.flashscorekz.com/hockey/russia/khl/fixtures/"
KHL_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

KHL_TEAMS = {
    "Авангард",
    "Автомобилист",
    "Адмирал",
    "Ак Барс",
    "Амур",
    "Барыс",
    "Витязь",
    "Динамо Москва",
    "Динамо Минск",
    "Шанхайские Драконы",
    "Лада",
    "Локомотив",
    "Металлург",
    "Нефтехимик",
    "Салават Юлаев",
    "Северсталь",
    "Сибирь",
    "СКА",
    "Сочи",
    "Спартак Москва",
    "Торпедо",
    "Трактор",
    "ЦСКА",
}

TEAM_EMOJI = {
    "Авангард": "🦅",
    "Автомобилист": "🚗",
    "Адмирал": "⚓",
    "Ак Барс": "🐆",
    "Амур": "🐯",
    "Барыс": "🐆",
    "Витязь": "🛡️",
    "Динамо Москва": "🔵",
    "Динамо Минск": "🔵",
    "Шанхайские Драконы": "🐉",
    "Лада": "🚘",
    "Локомотив": "🚂",
    "Металлург": "⚒️",
    "Нефтехимик": "🧪",
    "Салават Юлаев": "🟢",
    "Северсталь": "⚙️",
    "Сибирь": "❄️",
    "СКА": "⭐",
    "Сочи": "🌴",
    "Спартак Москва": "🔴",
    "Торпедо": "🏎️",
    "Трактор": "🚜",
    "ЦСКА": "🔴",
}

TEAM_CUSTOM_EMOJI = {
    "Торпедо": "5321177129252066955",
    "Амур": "5323509004436018932",
}

WAITING_FOR_EMOJI_ID = set()


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def extract_khl_value(block: str, key: str):
    pattern = rf"{re.escape(key)}÷(.*?)(?:¬|$)"
    match = re.search(pattern, block)
    return match.group(1).strip() if match else None


def parse_khl_match_block(block: str):
    home = extract_khl_value(block, "CX") or extract_khl_value(block, "AE")
    away = extract_khl_value(block, "AF")
    home_score = extract_khl_value(block, "AG")
    away_score = extract_khl_value(block, "AH")
    timestamp = extract_khl_value(block, "AD")

    if not home or not away:
        return None

    if home not in KHL_TEAMS or away not in KHL_TEAMS:
        return None

    if home_score is None or away_score is None:
        return None

    match_data = {
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "timestamp": timestamp,
        "date": None,
        "dt": None,
    }

    if timestamp and timestamp.isdigit():
        dt = datetime.datetime.fromtimestamp(int(timestamp), tz=MOSCOW_TZ)
        match_data["date"] = dt.strftime("%d.%m.%Y %H:%M")
        match_data["dt"] = dt

    return match_data


def parse_khl_fixture_block(block: str):
    home = extract_khl_value(block, "CX") or extract_khl_value(block, "AE")
    away = extract_khl_value(block, "AF")
    timestamp = extract_khl_value(block, "AD")
    home_score = extract_khl_value(block, "AG")
    away_score = extract_khl_value(block, "AH")

    if not home or not away:
        return None

    if home not in KHL_TEAMS or away not in KHL_TEAMS:
        return None

    match_data = {
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "timestamp": timestamp,
        "date": None,
        "dt": None,
    }

    if timestamp and timestamp.isdigit():
        dt = datetime.datetime.fromtimestamp(int(timestamp), tz=MOSCOW_TZ)
        match_data["date"] = dt.strftime("%d.%m.%Y %H:%M")
        match_data["dt"] = dt

    return match_data


def fetch_khl_matches():
    response = requests.get(KHL_URL, headers=KHL_HEADERS, timeout=30)
    response.raise_for_status()

    html = response.text
    raw_blocks = html.split("~AA÷")
    matches = []

    for block in raw_blocks:
        match_data = parse_khl_match_block(block)
        if match_data:
            matches.append(match_data)

    unique_matches = []
    seen = set()

    for match in matches:
        key = (
            match["home"],
            match["away"],
            match["timestamp"],
            match["home_score"],
            match["away_score"],
        )
        if key not in seen:
            seen.add(key)
            unique_matches.append(match)

    min_dt = MOSCOW_TZ.localize(datetime.datetime(1970, 1, 1))
    unique_matches.sort(
        key=lambda x: x["dt"] if x["dt"] else min_dt,
        reverse=True
    )

    return unique_matches


def fetch_khl_fixtures():
    response = requests.get(KHL_FIXTURES_URL, headers=KHL_HEADERS, timeout=30)
    response.raise_for_status()

    html = response.text
    raw_blocks = html.split("~AA÷")
    matches = []

    for block in raw_blocks:
        match_data = parse_khl_fixture_block(block)
        if match_data:
            matches.append(match_data)

    unique_matches = []
    seen = set()

    for match in matches:
        key = (
            match["home"],
            match["away"],
            match["timestamp"],
        )
        if key not in seen:
            seen.add(key)
            unique_matches.append(match)

    min_dt = MOSCOW_TZ.localize(datetime.datetime(1970, 1, 1))
    unique_matches.sort(
        key=lambda x: x["dt"] if x["dt"] else min_dt
    )

    return unique_matches


def build_team_token(team_name: str):
    if team_name in TEAM_CUSTOM_EMOJI:
        return {
            "text": "😀",
            "custom_emoji_id": TEAM_CUSTOM_EMOJI[team_name],
        }

    return {
        "text": TEAM_EMOJI.get(team_name, "🏒"),
        "custom_emoji_id": None,
    }


def build_text_and_entities_from_lines(lines):
    full_text = ""
    entities = []

    for line in lines:
        line_text = ""

        for part in line:
            if isinstance(part, dict) and "text" in part:
                token_text = part["text"]
                offset_utf16 = utf16_len(full_text + line_text)

                line_text += token_text

                custom_emoji_id = part.get("custom_emoji_id")
                if custom_emoji_id:
                    entities.append({
                        "type": "custom_emoji",
                        "offset": offset_utf16,
                        "length": utf16_len(token_text),
                        "custom_emoji_id": custom_emoji_id,
                    })
            else:
                line_text += str(part)

        full_text += line_text + "\n"

    return full_text.rstrip("\n"), entities


def send_message_with_entities(chat_id: int, text: str, entities=None):
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    if entities:
        payload["entities"] = entities

    logger.info(f"Отправка текста: {text!r}")
    logger.info(f"Отправка entities: {entities!r}")

    response = RAW_HTTP_SESSION.post(url, json=payload, timeout=30)

    logger.info(f"Telegram status_code: {response.status_code}")
    logger.info(f"Telegram response text: {response.text}")

    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")

    return result


def build_khl_scores_message():
    today_moscow = datetime.datetime.now(MOSCOW_TZ).date()

    logger.info("Запрашиваю данные КХЛ с Flashscore")
    matches = fetch_khl_matches()
    logger.info(f"Всего получено матчей КХЛ: {len(matches)}")

    if not matches:
        return f"🇷🇺 Результаты КХЛ за {today_moscow.strftime('%d.%m.%Y')}\n\nСегодня матчей КХЛ не было.", []

    today_matches = [m for m in matches if m["dt"] and m["dt"].date() == today_moscow]
    logger.info(f"Матчей КХЛ за сегодня: {len(today_matches)}")

    if not today_matches:
        return f"🇷🇺 Результаты КХЛ за {today_moscow.strftime('%d.%m.%Y')}\n\nСегодня матчей КХЛ не было.", []

    lines = []
    lines.append([f"🇷🇺 Результаты КХЛ за {today_moscow.strftime('%d.%m.%Y')}"])
    lines.append([""])

    for match in today_matches:
        home_token = build_team_token(match["home"])
        away_token = build_team_token(match["away"])

        lines.append([
            home_token, " ", match["home"], " ",
            match["home_score"], " : ", match["away_score"], " ",
            away_token, " ", match["away"]
        ])
        lines.append(["└ 🔴 Финальный счет"])
        lines.append([""])

    return build_text_and_entities_from_lines(lines)


def build_khl_day_message():
    today_moscow = datetime.datetime.now(MOSCOW_TZ).date()

    logger.info("Запрашиваю расписание КХЛ на текущий игровой день")
    matches = fetch_khl_fixtures()
    logger.info(f"Всего получено матчей КХЛ из fixtures: {len(matches)}")

    today_matches = [m for m in matches if m["dt"] and m["dt"].date() == today_moscow]
    logger.info(f"Матчей КХЛ на сегодня: {len(today_matches)}")

    if not today_matches:
        return f"📅 Матчи КХЛ на {today_moscow.strftime('%d.%m.%Y')}\n\nНа сегодня матчей не найдено.", []

    lines = []
    lines.append([f"📅 Матчи КХЛ на {today_moscow.strftime('%d.%m.%Y')}"])
    lines.append([""])

    for match in today_matches:
        home_token = build_team_token(match["home"])
        away_token = build_team_token(match["away"])

        if match["home_score"] is not None and match["away_score"] is not None:
            lines.append([
                home_token, " ", match["home"], " ",
                match["home_score"], " : ", match["away_score"], " ",
                away_token, " ", match["away"]
            ])
        else:
            start_time = match["dt"].strftime("%H:%M") if match["dt"] else "—:—"
            lines.append([
                home_token, " ", match["home"], " — ",
                away_token, " ", match["away"]
            ])
            lines.append([f"└ 🕒 Начало в {start_time} МСК"])

        lines.append([""])

    return build_text_and_entities_from_lines(lines)


def get_nhl_scores():
    return "🏒 Данные НХЛ временно недоступны. Возвращаемся к этому позже."


def build_test_custom_emoji_message():
    lines = [
        [
            {"text": "😀", "custom_emoji_id": TEAM_CUSTOM_EMOJI["Торпедо"]},
            " Торпедо — ",
            {"text": "😀", "custom_emoji_id": TEAM_CUSTOM_EMOJI["Амур"]},
            " Амур"
        ]
    ]
    return build_text_and_entities_from_lines(lines)


def extract_custom_emoji_ids_from_message(message):
    ids = []

    if not message.entities:
        return ids

    for entity in message.entities:
        entity_type = getattr(entity, "type", None)
        custom_emoji_id = getattr(entity, "custom_emoji_id", None)

        if custom_emoji_id is None:
            custom_emoji_id = getattr(entity, "customemojiid", None)

        if entity_type in ("custom_emoji", "customemoji") and custom_emoji_id:
            ids.append(str(custom_emoji_id))

    return ids


@bot.message_handler(commands=["start"])
def send_welcome(message):
    logger.info(f"Получена команда /start от chat_id={message.chat.id}")
    bot.reply_to(
        message,
        "Привет! Я бот с результатами матчей КХЛ и НХЛ.\n"
        "Команды:\n"
        "/khl — результаты КХЛ\n"
        "/day — матчи КХЛ текущего игрового дня\n"
        "/nhl — НХЛ временно недоступна\n"
        "/testemoji — тест custom emoji\n"
        "/getemojiid — получить custom emoji id\n"
        "/id — показать ваш chat id"
    )


@bot.message_handler(commands=["nhl"])
def send_nhl_now(message):
    logger.info(f"Получена команда /nhl от chat_id={message.chat.id}")
    bot.send_message(message.chat.id, "🏒 Данные НХЛ временно недоступны. Возвращаемся к этому позже.")


@bot.message_handler(commands=["khl"])
def send_khl_now(message):
    logger.info(f"Получена команда /khl от chat_id={message.chat.id}")
    bot.send_message(message.chat.id, "Запрашиваю данные по КХЛ...")

    try:
        text, entities = build_khl_scores_message()
        send_message_with_entities(message.chat.id, text, entities)
    except Exception:
        logger.exception("Ошибка при формировании/отправке результатов КХЛ")
        bot.send_message(message.chat.id, "⚠️ Произошла ошибка при получении данных КХЛ.")


@bot.message_handler(commands=["day", "today"])
def send_khl_day(message):
    logger.info(f"Получена команда /day от chat_id={message.chat.id}")
    bot.send_message(message.chat.id, "Смотрю матчи КХЛ на текущий игровой день...")

    try:
        text, entities = build_khl_day_message()
        send_message_with_entities(message.chat.id, text, entities)
    except Exception:
        logger.exception("Ошибка при формировании/отправке игрового дня КХЛ")
        bot.send_message(message.chat.id, "⚠️ Не удалось получить расписание матчей КХЛ на сегодня.")


@bot.message_handler(commands=["id"])
def send_chat_id(message):
    logger.info(f"Получена команда /id от chat_id={message.chat.id}")
    bot.reply_to(message, f"Ваш chat id: {message.chat.id}")


@bot.message_handler(commands=["testemoji"])
def send_testemoji(message):
    logger.info(f"Получена команда /testemoji от chat_id={message.chat.id}")

    try:
        text, entities = build_test_custom_emoji_message()
        send_message_with_entities(message.chat.id, text, entities)
    except Exception:
        logger.exception("Ошибка при отправке custom emoji сообщения")
        bot.send_message(message.chat.id, "⚠️ Не удалось отправить тестовое сообщение с custom emoji.")


@bot.message_handler(commands=["getemojiid"])
def getemojiid_start(message):
    logger.info(f"Получена команда /getemojiid от chat_id={message.chat.id}")
    WAITING_FOR_EMOJI_ID.add(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Отправь следующим сообщением один или несколько custom emoji из своего пака, и я верну их custom_emoji_id."
    )


@bot.message_handler(content_types=["text"])
def handle_text_message(message):
    if message.chat.id not in WAITING_FOR_EMOJI_ID:
        return

    WAITING_FOR_EMOJI_ID.discard(message.chat.id)

    ids = extract_custom_emoji_ids_from_message(message)

    if not ids:
        bot.send_message(message.chat.id, "Не нашёл custom emoji в сообщении. Попробуй ещё раз: /getemojiid")
        return

    lines = ["Найдены custom_emoji_id:"]
    for idx, custom_id in enumerate(ids, start=1):
        lines.append(f"{idx}. {custom_id}")

    bot.send_message(message.chat.id, "\n".join(lines))


def safe_send_to_subscribers_khl():
    if not AUTO_SEND_CHAT_IDS:
        logger.info("AUTO_SEND_CHAT_IDS не заданы, автосообщения КХЛ пропущены.")
        return

    try:
        text, entities = build_khl_scores_message()
    except Exception:
        logger.exception("Ошибка при подготовке автосообщения КХЛ")
        return

    for chat_id in AUTO_SEND_CHAT_IDS:
        try:
            send_message_with_entities(chat_id, text, entities)
            logger.info(f"Отправлено KHL-сообщение в chat id={chat_id}")
        except Exception:
            logger.exception(f"Ошибка отправки KHL-сообщения в chat id={chat_id}")


def safe_send_to_subscribers_nhl():
    if not AUTO_SEND_CHAT_IDS:
        logger.info("AUTO_SEND_CHAT_IDS не заданы, автосообщения НХЛ пропущены.")
        return

    text = get_nhl_scores()

    for chat_id in AUTO_SEND_CHAT_IDS:
        try:
            bot.send_message(chat_id, text)
            logger.info(f"Отправлено NHL-сообщение в chat id={chat_id}")
        except Exception:
            logger.exception(f"Ошибка отправки NHL-сообщения в chat id={chat_id}")


def scheduled_nhl():
    logger.info("Запуск запланированной отправки НХЛ")
    safe_send_to_subscribers_nhl()


def scheduled_khl():
    logger.info("Запуск запланированной отправки КХЛ")
    safe_send_to_subscribers_khl()


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)

    scheduler.add_job(
        scheduled_nhl,
        CronTrigger(hour=10, minute=0, timezone=MOSCOW_TZ),
        id="scheduled_nhl",
        replace_existing=True
    )

    scheduler.add_job(
        scheduled_khl,
        CronTrigger(hour=22, minute=0, timezone=MOSCOW_TZ),
        id="scheduled_khl",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Планировщик запущен.")
    logger.info(f"Загружены chat ids для автосообщений: {AUTO_SEND_CHAT_IDS}")
    return scheduler


def run_bot():
    logger.info("Бот начал опрос Telegram...")

    while True:
        try:
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                skip_pending=False,
                none_stop=True,
                interval=3
            )
        except Exception:
            logger.exception("Polling упал, перезапуск через 15 секунд...")
            time.sleep(15)


if __name__ == "__main__":
    logger.info("Бот запускается...")
    start_scheduler()
    run_bot()
