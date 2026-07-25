"""Entry point del bot: wiring di python-telegram-bot e polling."""
from __future__ import annotations

import functools
import io
import json
import logging
import os
from typing import Awaitable, Callable

import anthropic
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import handlers
import profile_ops
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


def _update_gia_processato(profile: dict, update: Update) -> bool:
    ultimo = profile.get("ultimo_update_id")
    return ultimo is not None and update.update_id <= ultimo


async def _profilo_per_update(update: Update) -> dict | None:
    """Carica il profilo e applica i due filtri comuni a tutti gli handler:
    chat autorizzata e update non già processato. None = non fare nulla."""
    profile = storage.load_profile()
    if not _chat_consentita(profile, update.effective_chat.id):
        return None
    if _update_gia_processato(profile, update):
        logger.info("Update %s già processato, ignoro", update.update_id)
        return None
    return profile


async def _rispondi(update: Update, profile: dict, messaggi: list[str]) -> None:
    if profile["chat_id"] is None:
        profile["chat_id"] = update.effective_chat.id
        logger.info("chat_id registrato: %s", profile["chat_id"])
    profile["ultimo_update_id"] = update.update_id
    storage.save_profile(profile)
    for messaggio in messaggi:
        await update.effective_message.reply_text(messaggio)


def _diagnostica_errore(exc: BaseException) -> tuple[str, str | None]:
    """Traduce un'eccezione nel messaggio da mostrare all'utente e, quando
    disponibile, nell'id di richiesta utile per il debug."""
    if isinstance(exc, (anthropic.APIStatusError, anthropic.APIConnectionError)):
        request_id = getattr(exc, "request_id", None)
        if request_id is None:
            risposta = getattr(exc, "response", None)
            if risposta is not None:
                request_id = risposta.headers.get("request-id")
        return ("Il servizio che uso per ragionare non risponde. Riprova fra un minuto.", request_id)
    if isinstance(exc, (requests.exceptions.RequestException, storage.StorageError, json.JSONDecodeError)):
        return ("Non riesco a leggere il tuo profilo in questo momento. Riprova fra poco.", None)
    return ("Qualcosa è andato storto, riprova tra un attimo.", None)


def _con_gestione_errori(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    """Avvolge un handler in un try/except: logga e avvisa l'utente in caso di errore."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await handler(update, context)
        except Exception as exc:
            chat_id = update.effective_chat.id if update.effective_chat else None
            messaggio_utente, request_id = _diagnostica_errore(exc)
            logger.exception(
                "Errore nell'handler %s (chat_id=%s, request_id=%s)",
                handler.__name__, chat_id, request_id,
            )
            if update.effective_message is not None:
                await update.effective_message.reply_text(messaggio_utente)

    return wrapper


@_con_gestione_errori
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    profile, messaggi = handlers.handle_start(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    profile, messaggi = handlers.handle_feedback_command(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def preferenze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    profile, messaggi = handlers.handle_preferenze_command(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    profile, messaggi = handlers.handle_reset_command(profile)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def sblocca_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    # Niente _rispondi: riassegnerebbe subito il chat_id appena liberato.
    profile = profile_ops.sblocca_chat(profile)
    profile["ultimo_update_id"] = update.update_id
    storage.save_profile(profile)
    await update.effective_message.reply_text(
        "Chat sbloccata: il prossimo che mi scrive verrà registrato come proprietario. "
        "(Se hai impostato ALLOWED_CHAT_ID su Railway, quella variabile ha comunque la precedenza.)"
    )


@_con_gestione_errori
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    # Documento e non testo: il profilo supera facilmente i 4096 caratteri
    # ammessi in un messaggio Telegram.
    payload = json.dumps(profile, ensure_ascii=False, indent=2).encode("utf-8")
    await update.effective_message.reply_document(
        document=io.BytesIO(payload), filename="profilo-mensa.json")
    profile["ultimo_update_id"] = update.update_id
    storage.save_profile(profile)


@_con_gestione_errori
async def messaggio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return

    testo = update.effective_message.text
    immagine_bytes = None
    media_type = None

    if update.effective_message.photo:
        file = await update.effective_message.photo[-1].get_file()
        immagine_bytes = bytes(await file.download_as_bytearray())
        media_type = "image/jpeg"  # Telegram ricomprime sempre le foto in JPEG
        testo = None
    elif update.effective_message.document:
        documento = update.effective_message.document
        file = await documento.get_file()
        immagine_bytes = bytes(await file.download_as_bytearray())
        media_type = documento.mime_type or ""
        testo = None

    profile, messaggi = handlers.handle_incoming_message(profile, testo, immagine_bytes, media_type)
    await _rispondi(update, profile, messaggi)


@_con_gestione_errori
async def non_gestito(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _profilo_per_update(update)
    if profile is None:
        return
    await _rispondi(update, profile, [
        "Non so gestire questo tipo di messaggio. Mandami il menu come testo o come "
        "foto, oppure usa /start, /feedback, /preferenze, /reset."
    ])


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("preferenze", preferenze_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("sbloccachat", sblocca_chat_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, messaggio
        )
    )
    # Catch-all: va registrato per ultimo, altrimenti intercetta tutto.
    app.add_handler(MessageHandler(filters.ALL, non_gestito))

    logger.info("Bot avviato in polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
