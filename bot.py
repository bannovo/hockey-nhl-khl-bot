import time
import logging
import datetime
import re

import pytz
import telebot
import requests

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


BOT_TOKEN = "8750772717:AAFC58ksrEf_jT9_9oEXDxTt2ew0lEWcjlY"
if BOT_TOKEN == "8750772717:AAFC58ksrEf_jT9_9oEXDxTt2ew0lEWcjlY":
    raise RuntimeError("Вставь токен бота в переменную BOT_TOKEN")

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ВШИТЫЙ chat id для проверки автоотправки
AUTO_SEND_CHAT_IDS = [188181889]

KHL_URL = "https://www.flashscorekz.com/hockey/russia/khl/results/"
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


def fetch_khl_matches():
    response = requests.get(KHL_URL, headers=KHL_HEADERS, timeout=20)
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


def get_khl_scores():
    today_moscow = datetime.datetime.now(MOSCOW_TZ).date()

    try:
        logger.info("Запрашиваю данные КХЛ с Flashscore")
        matches = fetch_khl_matches()
        logger.info(f"Всего получено матчей КХЛ: {len(matches)}")

        if not matches:
            return f"🇷🇺 **Результаты КХЛ за {today_moscow.strftime('%d.%m.%Y')}**\n\nСегодня матчей КХЛ не было."

        today_matches = [m for m in matches if m["dt"] and m["dt"].date() == today_moscow]
        logger.info(f"Матчей КХЛ за сегодня: {len(today_matches)}")

        if not today_matches:
            return f"🇷🇺 **Результаты КХЛ за {today_moscow.strftime('%d.%m.%Y')}**\n\nСегодня матчей КХЛ не было."

        message = f"🇷🇺 **Результаты КХЛ за {today_moscow.strftime('%d.%m.%Y')}**\n\n"

        for match in today_matches:
            message += (
                f"{match['away']} **{match['away_score']}** : **{match['home_score']}** {match['home']}\n"
                f"└ 🔴 Финальный счет\n\n"
            )

        logger.info("Сообщение по КХЛ успешно сформировано")
        return message

    except Exception:
        logger.exception("Ошибка при получении данных КХЛ")
        return "⚠️ Произошла ошибка при получении данных КХЛ."


def get_nhl_scores():
    return "🏒 Данные НХЛ временно недоступны. Возвращаемся к этому позже."


@bot.message_handler(commands=["start"])
def send_welcome(message):
    logger.info(f"Получена команда /start от chat_id={message.chat.id}")
    bot.reply_to(
        message,
        "Привет! Я бот с результатами матчей КХЛ и НХЛ.\n"
        "Я автоматически присылаю результаты:\n"
        "🇷🇺 КХЛ в 22:00 по Москве\n"
        "🇺🇸 НХЛ временно недоступна\n"
        "Используй команды /nhl или /khl, чтобы получить результаты сейчас."
    )


@bot.message_handler(commands=["nhl"])
def send_nhl_now(message):
    logger.info(f"Получена команда /nhl от chat_id={message.chat.id}")
    bot.send_message(message.chat.id, "🏒 Данные НХЛ временно недоступны. Возвращаемся к этому позже.")


@bot.message_handler(commands=["khl"])
def send_khl_now(message):
    logger.info(f"Получена команда /khl от chat_id={message.chat.id}")
    bot.send_message(message.chat.id, "Запрашиваю данные по КХЛ...")
    result = get_khl_scores()
    bot.send_message(message.chat.id, result)


@bot.message_handler(commands=["id"])
def send_chat_id(message):
    logger.info(f"Получена команда /id от chat_id={message.chat.id}")
    bot.reply_to(message, f"Ваш chat id: {message.chat.id}")


def safe_send_to_subscribers(text: str):
    if not AUTO_SEND_CHAT_IDS:
        logger.info("AUTO_SEND_CHAT_IDS не заданы, автосообщения пропущены.")
        return

    for chat_id in AUTO_SEND_CHAT_IDS:
        try:
            bot.send_message(chat_id, text)
            logger.info(f"Отправлено сообщение в chat id={chat_id}")
        except Exception:
            logger.exception(f"Ошибка отправки сообщения в chat id={chat_id}")


def scheduled_nhl():
    logger.info("Запуск запланированной отправки НХЛ (заглушка)")
    message = get_nhl_scores()
    safe_send_to_subscribers(message)


def scheduled_khl():
    logger.info("Запуск запланированной отправки КХЛ")
    message = get_khl_scores()
    safe_send_to_subscribers(message)


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)

    scheduler.add_job(
        scheduled_nhl,
        CronTrigger(hour=10, minute=0, timezone=MOSCOW_TZ)
    )

    scheduler.add_job(
        scheduled_khl,
        CronTrigger(hour=10, minute=45, timezone=MOSCOW_TZ)
    )

    scheduler.start()
    logger.info("Планировщик запущен.")
    logger.info(f"Загружены chat ids для автосообщений: {AUTO_SEND_CHAT_IDS}")
    return scheduler


def run_bot():
    logger.info("Бот начал опрос Telegram...")

    try:
        bot.remove_webhook()
        logger.info("Webhook удален.")
    except Exception:
        logger.exception("Не удалось удалить webhook")

    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=True
            )
        except Exception:
            logger.exception("Polling упал, перезапуск через 15 секунд...")
            time.sleep(15)


if __name__ == "__main__":
    logger.info("Бот запускается...")
    start_scheduler()
    run_bot()
