"""Persistenza del profilo utente su Upstash Redis (via REST API).

Il piano Railway Trial non supporta volumi persistenti, quindi il profilo
non vive su disco: viene serializzato come stringa JSON e salvato in
un'unica chiave Redis.
"""
from __future__ import annotations

import datetime
import json
import logging
import os

import requests

import profile_ops

logger = logging.getLogger(__name__)

LEGACY_PROFILE_KEY = "mensa_bot:profile"
PROFILE_KEY_FMT = "mensa_bot:profile:{chat_id}"
PROFILE_BACKUP_KEY_FMT = "mensa_bot:profile:backup:{chat_id}:{giorno}"

# Re-export intenzionale: lo schema del profilo vive in profile_ops (che non
# deve dipendere dalla persistenza), ma storage resta il punto di accesso
# storico per il resto del codice e per i test.
DEFAULT_PROFILE: dict = profile_ops.DEFAULT_PROFILE


class StorageError(RuntimeError):
    """Risposta inattesa dal backend di persistenza (non è un errore di rete)."""


def _base_url() -> str:
    return os.environ["UPSTASH_REDIS_REST_URL"].rstrip("/")


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['UPSTASH_REDIS_REST_TOKEN']}"}


def _profile_key(chat_id: int) -> str:
    return PROFILE_KEY_FMT.format(chat_id=chat_id)


def _migra_profilo_legacy(chat_id: int) -> dict | None:
    """Copia il profilo dalla vecchia chiave fissa (mono-utente, pre-refactor)
    a quella per-chat, ma solo se appartiene a questo chat_id — altrimenti
    resta un profilo altrui orfano sotto la chiave legacy. Chiamata solo
    quando la chiave per-chat è ancora assente, quindi non pesa sul percorso
    comune (profilo già migrato o mai esistito)."""
    resp = requests.get(f"{_base_url()}/get/{LEGACY_PROFILE_KEY}", headers=_headers(), timeout=10)
    resp.raise_for_status()
    result = resp.json().get("result")
    if result is None:
        return None

    try:
        dati = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(dati, dict) or dati.get("chat_id") != chat_id:
        return None

    profilo = profile_ops.normalizza_profilo(dati)
    save_profile(chat_id, profilo)
    resp = requests.post(f"{_base_url()}/del/{LEGACY_PROFILE_KEY}", headers=_headers(), timeout=10)
    resp.raise_for_status()
    logger.info("Profilo legacy migrato alla chiave per-chat (chat_id=%s)", chat_id)
    return profilo


def load_profile(chat_id: int) -> dict:
    resp = requests.get(f"{_base_url()}/get/{_profile_key(chat_id)}", headers=_headers(), timeout=10)
    resp.raise_for_status()
    body = resp.json()

    if "result" not in body:
        # Risposta inattesa (errore applicativo di Upstash, proxy, ...): non
        # sappiamo se la chiave esiste, quindi non sovrascriviamo nulla.
        raise StorageError(f"Risposta Upstash senza campo 'result': {body!r}")

    result = body["result"]
    if result is None:
        migrato = _migra_profilo_legacy(chat_id)
        if migrato is not None:
            return migrato
        # Nessun profilo legacy da migrare: primo avvio per questa chat.
        profilo = profile_ops.profilo_vuoto(chat_id)
        save_profile(chat_id, profilo)
        return profilo

    dati = json.loads(result)
    if not isinstance(dati, dict):
        raise StorageError(f"Il profilo salvato non è un oggetto JSON: {dati!r}")
    return profile_ops.normalizza_profilo(dati)


def _backup_key(chat_id: int) -> str:
    return PROFILE_BACKUP_KEY_FMT.format(chat_id=chat_id, giorno=datetime.date.today().weekday())


def save_profile(chat_id: int, profile: dict) -> None:
    payload = json.dumps(profile)
    resp = requests.post(
        f"{_base_url()}/set/{_profile_key(chat_id)}",
        headers=_headers(),
        data=payload.encode("utf-8"),
        timeout=10,
    )
    resp.raise_for_status()

    # Backup rotante su 7 chiavi per chat (una per giorno della settimana): un
    # suo fallimento non deve mai far fallire il salvataggio principale.
    try:
        backup = requests.post(
            f"{_base_url()}/set/{_backup_key(chat_id)}",
            headers=_headers(),
            data=payload.encode("utf-8"),
            timeout=10,
        )
        backup.raise_for_status()
    except requests.exceptions.RequestException:
        logger.warning("Backup del profilo fallito, proseguo", exc_info=True)
