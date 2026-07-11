"""Entry point del bot: wiring di python-telegram-bot e polling."""
from __future__ import annotations

import functools
import logging
import os
from typing import Awaitable, Callable

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import handlers
import storage

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _chat_consentita(profile: dict, chat_id: int) -> bool:
    allowed_env = os.environ.get("ALLOWED_CHAT_ID")
    if allowed_env:
        return str(chat_id) == allowed_env
    if profile["chat_id"] is None:
        return True
    return profile["chat_id"] == chat_id


async def _rispondi(update: Update, profile: dict, messaggi: list[str]) -> None:
    if profile["chat_id"] is None:
        profile["chat_id"] = update.effective_chat.id
        logger.info("chat_id registrato: %s", profile["chat_id"])
    storage.save_profile(profile)
    for messaggio in messaggi:
        await update.message.reply_text(messaggio)


def _con_gestione_errori(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    """Avvolge un handler in un try/except: logga e avvisa l'utente in caso di errore."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await handler(update, context)
        except Exception:
            chat_id = update.effective_chat.id if update.effective_chat else None
            logger.exception("Errore nell'handler %s (chat_id=%s)", handler.__name__, chat_id)
            if update.message is not None:
                await update.message.reply_text("Qualcosa è andato storto, riprova tra un attimo.")

    return wrapper


@_con_gestione_errori
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = storage.load_profile()
    if not _chat_consentita(profile, update.effective_chat.id):
        return
    profile, messaggi = handlers.handle_start(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = storage.load_profile()
    if not _chat_consentita(profile, update.effective_chat.id):
        return
    profile, messaggi = handlers.handle_feedback_command(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def preferenze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = storage.load_profile()
    if not _chat_consentita(profile, update.effective_chat.id):
        return
    profile, messaggi = handlers.handle_preferenze_command(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = storage.load_profile()
    if not _chat_consentita(profile, update.effective_chat.id):
        return
    profile, messaggi = handlers.handle_reset_command(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def messaggio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = storage.load_profile()
    if not _chat_consentita(profile, update.effective_chat.id):
        return

    testo = update.message.text
    immagine_bytes = None
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        immagine_bytes = bytes(await file.download_as_bytearray())
        testo = None

    profile, messaggi = handlers.handle_incoming_message(profile, testo, immagine_bytes)
    await _rispondi(update, profile, messaggi)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("preferenze", preferenze_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, messaggio))

    logger.info("Bot avviato in polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
