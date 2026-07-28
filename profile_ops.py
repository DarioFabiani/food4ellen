"""Logica pura di manipolazione del profilo (nessuna I/O di rete)."""
from __future__ import annotations

import copy
import logging
import uuid
from datetime import date

logger = logging.getLogger(__name__)

MAX_PASTI_RECENTI = 20

# Finestra di conversazione tenuta nel profilo: serve a risolvere i riferimenti
# al turno precedente ("perfetta", "quella di ieri"), non a ricostruire la
# storia completa. Il tetto sui caratteri evita che un singolo menu incollato
# occupi da solo tutta la finestra.
MAX_CRONOLOGIA = 20
MAX_CARATTERI_TURNO = 500

RUOLI_CRONOLOGIA = {"utente", "bot"}

DEFAULT_PROFILE: dict = {
    "chat_id": None,
    "onboarding_completato": False,
    "allergie_intolleranze": [],
    "preferenze": [],
    "pasti_recenti": [],
    "riassunto_storico": "",
    "cronologia": [],
    "in_attesa_di_conferma_reset": False,
    "ultimo_update_id": None,
}

SENTIMENT_VALIDI = {"like", "dislike", "neutro"}


def profilo_vuoto(chat_id=None) -> dict:
    profilo = copy.deepcopy(DEFAULT_PROFILE)
    profilo["chat_id"] = chat_id
    return profilo


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


def normalizza_turno(turno) -> dict | None:
    """Restituisce il turno di conversazione con le sole due chiavi previste, o
    None se la voce è inutilizzabile."""
    if not isinstance(turno, dict):
        return None

    ruolo = turno.get("ruolo")
    if ruolo not in RUOLI_CRONOLOGIA:
        return None

    testo = turno.get("testo")
    if not isinstance(testo, str) or not testo.strip():
        return None

    return {"ruolo": ruolo, "testo": testo[:MAX_CARATTERI_TURNO]}


def normalizza_profilo(dati: dict) -> dict:
    """Fonde i dati letti dalla persistenza con DEFAULT_PROFILE (così un campo
    nuovo nello schema non rompe i profili esistenti) e ripulisce le preferenze."""
    profilo = {**copy.deepcopy(DEFAULT_PROFILE), **dati}

    # Whitelist: senza questa, un campo rimosso dallo schema resterebbe nel blob
    # salvato per sempre, perché il merge qui sopra ricopia tutto ciò che trova.
    profilo = {chiave: profilo[chiave] for chiave in DEFAULT_PROFILE}

    for campo in ("allergie_intolleranze", "pasti_recenti", "preferenze", "cronologia"):
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

    cronologia = []
    for turno in profilo["cronologia"]:
        normalizzato = normalizza_turno(turno)
        if normalizzato is None:
            logger.warning("Turno di cronologia scartato perché non recuperabile: %r", turno)
            continue
        cronologia.append(normalizzato)
    # Il tetto va riapplicato in lettura e non solo in scrittura: un blob
    # manomesso o scritto da una versione precedente gonfierebbe il prompt.
    profilo["cronologia"] = cronologia[-MAX_CRONOLOGIA:]

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

    if not isinstance(profilo["onboarding_completato"], bool):
        logger.warning("onboarding_completato non è un booleano, ripristino il default")
        profilo["onboarding_completato"] = DEFAULT_PROFILE["onboarding_completato"]

    chat_id = profilo["chat_id"]
    if chat_id is not None and (isinstance(chat_id, bool) or not isinstance(chat_id, int)):
        # Un chat_id non intero romperebbe il confronto in
        # storage._migra_profilo_legacy (che si aspetta un intero da
        # confrontare con l'id della chat che sta scrivendo).
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


def aggiorna_allergie(profile: dict, voci: list[str], modo: str = "aggiungi") -> tuple[dict, list[str]]:
    """Aggiunge o sostituisce le allergie/intolleranze. Restituisce il profilo
    aggiornato e l'elenco finale, che il chiamante mostra all'utente: una
    modifica silenziosa di un vincolo assoluto non è accettabile."""
    profile = copy.deepcopy(profile)

    partenza = [] if modo == "sostituisci" else list(profile["allergie_intolleranze"])
    finale: list[str] = []
    viste: set[str] = set()
    for voce in [*partenza, *voci]:
        if not isinstance(voce, str):
            logger.warning("Voce di allergia scartata perché non è una stringa: %r", voce)
            continue
        pulita = voce.strip()
        if not pulita or pulita.casefold() in viste:
            continue
        viste.add(pulita.casefold())
        finale.append(pulita)

    profile["allergie_intolleranze"] = finale
    return profile, finale


def aggiungi_a_cronologia(profile: dict, ruolo: str, testo: str) -> dict:
    """Appende un turno alla finestra di conversazione e la tronca. È l'unico
    punto in cui la cronologia cresce, così il tetto ha un solo posto in cui
    poter sbagliare."""
    turno = normalizza_turno({"ruolo": ruolo, "testo": testo})
    if turno is None:
        return profile

    profile = copy.deepcopy(profile)
    profile["cronologia"] = [*profile["cronologia"], turno][-MAX_CRONOLOGIA:]
    return profile


def record_new_meal(
    profile: dict, opzioni_presentate: list[str], scelta_consigliata: str
) -> tuple[dict, str]:
    """Registra il pasto e restituisce (profilo, id): l'id serve all'agente per
    agganciarci il feedback nei turni successivi."""
    profile = copy.deepcopy(profile)
    pasto_id = uuid.uuid4().hex
    profile["pasti_recenti"].append(
        {
            "id": pasto_id,
            "data": date.today().isoformat(),
            "opzioni_presentate": opzioni_presentate,
            "scelta_consigliata": scelta_consigliata,
            "scelta_reale": None,
            "feedback": None,
            "gradimento": None,
        }
    )
    return profile, pasto_id


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
