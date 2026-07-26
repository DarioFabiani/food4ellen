"""Logica pura di manipolazione del profilo (nessuna I/O di rete)."""
from __future__ import annotations

import copy
import logging
import uuid
from datetime import date

logger = logging.getLogger(__name__)

MAX_PASTI_RECENTI = 20

DEFAULT_PROFILE: dict = {
    "chat_id": None,
    "onboarding_completato": False,
    "onboarding_step": 1,
    "allergie_intolleranze": [],
    "preferenze": [],
    "pasti_recenti": [],
    "riassunto_storico": "",
    "in_attesa_di_feedback_per": None,
    "in_attesa_di_conferma_reset": False,
    "ultimo_update_id": None,
}

SENTIMENT_VALIDI = {"like", "dislike", "neutro"}


def profilo_vuoto(chat_id=None) -> dict:
    profilo = copy.deepcopy(DEFAULT_PROFILE)
    profilo["chat_id"] = chat_id
    return profilo


def sblocca_chat(profile: dict) -> dict:
    profile = copy.deepcopy(profile)
    profile["chat_id"] = None
    return profile


def normalizza_preferenza(pref) -> dict | None:
    """Restituisce la preferenza con esattamente le 5 chiavi previste, o None se
    la voce è inutilizzabile (campo obbligatorio mancante o di tipo sbagliato)."""
    if not isinstance(pref, dict):
        return None

    item = pref.get("item")
    if not isinstance(item, str) or not item.strip():
        return None

    sentiment = pref.get("sentiment")
    if sentiment not in SENTIMENT_VALIDI:
        return None

    peso = pref.get("peso")
    if isinstance(peso, bool) or not isinstance(peso, int) or not 1 <= peso <= 5:
        return None

    fonte = pref.get("fonte")
    if not isinstance(fonte, str) or not fonte.strip():
        fonte = "inferito"

    note = pref.get("note")
    if not isinstance(note, str):
        note = None

    return {
        "item": item,
        "sentiment": sentiment,
        "peso": peso,
        "fonte": fonte,
        "note": note,
    }


def normalizza_profilo(dati: dict) -> dict:
    """Fonde i dati letti dalla persistenza con DEFAULT_PROFILE (così un campo
    nuovo nello schema non rompe i profili esistenti) e ripulisce le preferenze."""
    profilo = {**copy.deepcopy(DEFAULT_PROFILE), **dati}

    for campo in ("allergie_intolleranze", "pasti_recenti", "preferenze"):
        if not isinstance(profilo[campo], list):
            logger.warning("Campo %r non è una lista, ripristino il default", campo)
            profilo[campo] = copy.deepcopy(DEFAULT_PROFILE[campo])

    preferenze = []
    for pref in profilo["preferenze"]:
        normalizzata = normalizza_preferenza(pref)
        if normalizzata is None:
            logger.warning("Preferenza scartata perché non recuperabile: %r", pref)
            continue
        preferenze.append(normalizzata)
    profilo["preferenze"] = preferenze

    if not isinstance(profilo["riassunto_storico"], str):
        logger.warning("riassunto_storico non è una stringa, ripristino il default")
        profilo["riassunto_storico"] = ""

    ultimo_update_id = profilo["ultimo_update_id"]
    if ultimo_update_id is not None and (
        isinstance(ultimo_update_id, bool) or not isinstance(ultimo_update_id, int)
    ):
        # Un tipo sbagliato qui fa esplodere bot._update_gia_processato prima di
        # ogni dispatch: il bot diventerebbe irrecuperabile da Telegram.
        logger.warning("ultimo_update_id non è un intero, ripristino il default")
        profilo["ultimo_update_id"] = DEFAULT_PROFILE["ultimo_update_id"]

    onboarding_step = profilo["onboarding_step"]
    if (
        isinstance(onboarding_step, bool)
        or not isinstance(onboarding_step, int)
        or not 1 <= onboarding_step <= 4
    ):
        logger.warning("onboarding_step non valido (%r), ripristino il default", onboarding_step)
        profilo["onboarding_step"] = DEFAULT_PROFILE["onboarding_step"]

    chat_id = profilo["chat_id"]
    if chat_id is not None and (isinstance(chat_id, bool) or not isinstance(chat_id, int)):
        # Un chat_id non intero rende _chat_consentita sempre False: il bot
        # smetterebbe di rispondere in silenzio.
        logger.warning("chat_id non è un intero, ripristino il default")
        profilo["chat_id"] = DEFAULT_PROFILE["chat_id"]

    return profilo


def merge_preferenze(profile: dict, nuove_preferenze: list[dict]) -> dict:
    profile = copy.deepcopy(profile)

    esistenti: dict[str, dict] = {}
    for pref in profile["preferenze"]:
        normalizzata = normalizza_preferenza(pref)
        if normalizzata is None:
            logger.warning("Preferenza già presente scartata perché non recuperabile: %r", pref)
            continue
        esistenti[normalizzata["item"]] = normalizzata

    for nuova in nuove_preferenze:
        normalizzata = normalizza_preferenza(nuova)
        if normalizzata is None:
            logger.warning("Nuova preferenza scartata perché non recuperabile: %r", nuova)
            continue
        esistenti[normalizzata["item"]] = normalizzata

    profile["preferenze"] = list(esistenti.values())
    return profile


def record_new_meal(profile: dict, opzioni_presentate: list[str], scelta_consigliata: str) -> dict:
    profile = copy.deepcopy(profile)
    profile["pasti_recenti"].append(
        {
            "id": uuid.uuid4().hex,
            "data": date.today().isoformat(),
            "opzioni_presentate": opzioni_presentate,
            "scelta_consigliata": scelta_consigliata,
            "scelta_reale": None,
            "feedback": None,
            "gradimento": None,
        }
    )
    return profile


def find_pasto_in_attesa_di_feedback(profile: dict) -> dict | None:
    for pasto in reversed(profile["pasti_recenti"]):
        if pasto["feedback"] is None:
            return pasto
    return None


def apply_feedback(
    profile: dict,
    pasto_id: str,
    gradimento: str,
    scelta_reale: str | None,
    feedback_testo: str,
    nuove_preferenze: list[dict],
) -> dict:
    profile = copy.deepcopy(profile)
    for pasto in profile["pasti_recenti"]:
        if pasto["id"] == pasto_id:
            pasto["feedback"] = feedback_testo
            pasto["gradimento"] = gradimento
            if scelta_reale:
                pasto["scelta_reale"] = scelta_reale
            break
    return merge_preferenze(profile, nuove_preferenze)


def pasto_piu_vecchio_da_archiviare(profile: dict) -> dict | None:
    if len(profile["pasti_recenti"]) > MAX_PASTI_RECENTI:
        return profile["pasti_recenti"][0]
    return None


def archivia_pasto_piu_vecchio(profile: dict, nuovo_riassunto: str) -> dict:
    profile = copy.deepcopy(profile)
    profile["pasti_recenti"] = profile["pasti_recenti"][1:]
    profile["riassunto_storico"] = nuovo_riassunto
    return profile
