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

PROFILE_KEY = "mensa_bot:profile"
PROFILE_BACKUP_KEY_FMT = "mensa_bot:profile:backup:{giorno}"

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


def load_profile() -> dict:
    resp = requests.get(f"{_base_url()}/get/{PROFILE_KEY}", headers=_headers(), timeout=10)
    resp.raise_for_status()
    body = resp.json()

    if "result" not in body:
        # Risposta inattesa (errore applicativo di Upstash, proxy, ...): non
        # sappiamo se la chiave esiste, quindi non sovrascriviamo nulla.
        raise StorageError(f"Risposta Upstash senza campo 'result': {body!r}")

    result = body["result"]
    if result is None:
        # Chiave assente: primo avvio, creiamo il profilo vuoto.
        profilo = profile_ops.profilo_vuoto()
        save_profile(profilo)
        return profilo

    dati = json.loads(result)
    if not isinstance(dati, dict):
        raise StorageError(f"Il profilo salvato non è un oggetto JSON: {dati!r}")
    return profile_ops.normalizza_profilo(dati)


def _backup_key() -> str:
    return PROFILE_BACKUP_KEY_FMT.format(giorno=datetime.date.today().weekday())


def save_profile(profile: dict) -> None:
    payload = json.dumps(profile)
    resp = requests.post(
        f"{_base_url()}/set/{PROFILE_KEY}",
        headers=_headers(),
        data=payload.encode("utf-8"),
        timeout=10,
    )
    resp.raise_for_status()

    # Backup rotante su 7 chiavi (una per giorno della settimana): un suo
    # fallimento non deve mai far fallire il salvataggio principale.
    try:
        backup = requests.post(
            f"{_base_url()}/set/{_backup_key()}",
            headers=_headers(),
            data=payload.encode("utf-8"),
            timeout=10,
        )
        backup.raise_for_status()
    except requests.exceptions.RequestException:
        logger.warning("Backup del profilo fallito, proseguo", exc_info=True)
