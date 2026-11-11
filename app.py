"""Telegram-бот @bahbenzin_bot — цены на топливо в Уфе.

Версия для Render (webhook-режим): бот «просыпается» по сообщению,
работает 24/7 даже когда локальный ПК выключен.
"""
import json
import logging
import os

from fastapi import FastAPI, Header, Request, Response
import uvicorn

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("BOT_TOKEN", "9276147910:AAHa6VojnxcFs8GUI6PDeTmKgyj3CFjAE7U")
# Секретный токен для проверки, что запрос пришёл от Telegram (обязателен на Render)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_PATH = "/webhook"

GOOD_WORK = "good work"
TRIGGER_WORDS = ("cjj", "otybz", "pipi")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIONS_FILE = os.path.join(BASE_DIR, "stations.json")

FUEL_LABELS = [
    ("92", "АИ-92"),
    ("95", "АИ-95"),
    ("98", "АИ-98"),
    ("100", "АИ-100"),
    ("ДТ", "Дизель (ДТ)"),
]


# ---------------------------------------------------------------------------
# Данные по заправкам
# ---------------------------------------------------------------------------
def load_stations() -> list:
    try:
        with open(STATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


STATIONS = load_stations()
STATION_BY_ID = {str(s.get("station_id")): s for s in STATIONS}


def fmt_price(value) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value).replace(".", ",")


def station_button_label(station: dict) -> str:
    brand = station.get("brand") or "АЗС"
    addr = station.get("address") or ""
    return f"{brand} · {addr}".strip(" ·")[:60]


def build_station_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for s in STATIONS:
        sid = str(s.get("station_id"))
        rows.append([InlineKeyboardButton(station_button_label(s), callback_data=f"fuel:{sid}")])
    return InlineKeyboardMarkup(rows)


def build_details_text(station: dict) -> str:
    brand = station.get("brand") or "АЗС"
    addr = station.get("address") or "—"
    prices = station.get("prices") or {}
    lines = [f"⛽ {brand}", f"📍 {addr}", "", "💰 Цены на топливо (Уфа):"]
    shown = False
    for key, label in FUEL_LABELS:
        price = prices.get(key)
        if price:
            lines.append(f"• {label} — {fmt_price(price)} ₽/л")
            shown = True
    if not shown:
        lines.append("Нет данных о ценах")
    lines.append("")
    lines.append("Данные: benzonavt.ru")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------
async def good_work(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(GOOD_WORK)


def is_trigger(text: str) -> bool:
    words = text.strip().lower().replace("/", "").split()
    return any(word in words for word in TRIGGER_WORDS)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.text and is_trigger(update.message.text):
        await update.message.reply_text(GOOD_WORK)


async def fuel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not STATIONS:
        text = "Данные о заправках пока недоступны."
    else:
        text = f"⛽ Выберите заправку (Уфа):\nВсего: {len(STATIONS)}"
    await update.effective_message.reply_text(text, reply_markup=build_station_keyboard())


async def fuel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("fuel:"):
        return
    sid = data.split(":", 1)[1]
    station = STATION_BY_ID.get(sid)
    if not station:
        await query.edit_message_text("Заправка не найдена.")
        return
    back = [[InlineKeyboardButton("← К списку заправок", callback_data="fuel:menu")]]
    await query.edit_message_text(
        build_details_text(station),
        reply_markup=InlineKeyboardMarkup(back),
    )


async def fuel_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not STATIONS:
        text = "Данные о заправках пока недоступны."
    else:
        text = f"⛽ Выберите заправку (Уфа):\nВсего: {len(STATIONS)}"
    await query.edit_message_text(text, reply_markup=build_station_keyboard())


# ---------------------------------------------------------------------------
# Регистрация обработчиков
# ---------------------------------------------------------------------------
application = Application.builder().token(TOKEN).build()

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
for word in TRIGGER_WORDS:
    application.add_handler(CommandHandler(word, good_work))
application.add_handler(CommandHandler("fuel", fuel_menu))
application.add_handler(CallbackQueryHandler(fuel_back, pattern="^fuel:menu$"))
application.add_handler(CallbackQueryHandler(fuel_callback, pattern="^fuel:"))


# ---------------------------------------------------------------------------
# Веб-сервер (FastAPI)
# ---------------------------------------------------------------------------
api = FastAPI(title="bahbenzin-bot")


@api.get("/")
async def health():
    """Здоровье сервиса (для Render и внешних пингов)."""
    return {"status": "ok", "stations": len(STATIONS)}


@api.post(WEBHOOK_PATH)
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    """Приём обновлений от Telegram."""
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        return Response(status_code=403)
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response(status_code=200)


@api.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    external = os.environ.get("RENDER_EXTERNAL_URL", "")
    if external:
        url = f"{external.rstrip('/')}{WEBHOOK_PATH}"
        await application.bot.set_webhook(
            url,
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=["message", "callback_query"],
        )
        logging.info("Webhook установлен: %s", url)


@api.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(api, host="0.0.0.0", port=port)
