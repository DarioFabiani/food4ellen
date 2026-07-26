"""Logica di business per ogni comando/messaggio del bot.

Le funzioni qui non toccano né Telegram né la persistenza: ricevono il
profilo corrente e l'input dell'utente, chiamano claude_client quando serve,
e restituiscono (profilo_aggiornato, messaggi_da_inviare). bot.py chiama
queste funzioni e si occupa di leggere/scrivere il profilo e di parlare con
Telegram.

Il grosso della conversazione passa da `processa_messaggio`, che delega
all'agente. Restano deterministici solo i percorsi in cui una decisione del
modello non aggiungerebbe nulla o sarebbe pericolosa: la conferma del reset,
il riepilogo delle preferenze e la manutenzione dello storico.
"""
from __future__ import annotations

import copy
import logging

import claude_client
import profile_ops
import prompts

logger = logging.getLogger(__name__)

# Formati immagine accettati dall'API Anthropic.
MEDIA_TYPE_IMMAGINE_SUPPORTATI = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMMAGINE_BYTES = 3_500_000

SEME_START = "L'utente ha appena avviato il bot."
SEME_FEEDBACK = "L'utente vuole raccontarti com'è andato l'ultimo pasto non ancora valutato."


def handle_preferenze_command(profile: dict) -> tuple[dict, list[str]]:
    """Riepilogo deterministico: è una lettura di dati locali, farla passare
    dall'agente costerebbe una chiamata e aprirebbe la porta a un riepilogo
    inventato."""
    righe = ["Allergie/intolleranze: " + (", ".join(profile["allergie_intolleranze"]) or "nessuna")]
    if profile["preferenze"]:
        righe.append("Preferenze:")
        for p in profile["preferenze"]:
            nota = f" ({p['note']})" if p.get("note") else ""
            righe.append(f"- {p['item']}: {p['sentiment']}, peso {p['peso']}{nota}")
    else:
        righe.append("Nessuna preferenza registrata ancora.")
    return profile, ["\n".join(righe)]


def handle_reset_command(profile: dict) -> tuple[dict, list[str]]:
    profile = copy.deepcopy(profile)
    profile["in_attesa_di_conferma_reset"] = True
    return profile, [
        "Sei sicura di voler azzerare tutto il profilo? Rispondi CONFERMA per "
        "procedere, qualsiasi altra cosa per annullare."
    ]


def handle_reset_confirmation(profile: dict, risposta_utente: str) -> tuple[dict, list[str]]:
    if risposta_utente.strip().upper() != "CONFERMA":
        profile = copy.deepcopy(profile)
        profile["in_attesa_di_conferma_reset"] = False
        return profile, ["Reset annullato."]

    nuovo_profilo = profile_ops.profilo_vuoto(profile.get("chat_id"))
    return nuovo_profilo, [
        "Profilo azzerato. Ricominciamo da capo: hai allergie o intolleranze "
        "alimentari? Sono vincoli assoluti, non ti consiglierò mai nulla che le contenga."
    ]


def _manutenzione_storico(profile: dict) -> dict:
    """Comprime il pasto più vecchio quando lo storico supera la finestra.

    Sta fuori dai tool di proposito: è manutenzione interna, e un suo
    fallimento non deve diventare un errore su cui l'agente si mette a
    ragionare. Se fallisce, la risposta all'utente parte lo stesso.
    """
    da_archiviare = profile_ops.pasto_piu_vecchio_da_archiviare(profile)
    if da_archiviare is None:
        return profile

    try:
        nuovo_riassunto = claude_client.update_riassunto_storico(
            profile["riassunto_storico"], da_archiviare
        )
        return profile_ops.archivia_pasto_piu_vecchio(profile, nuovo_riassunto)
    except Exception:
        logger.exception("Archiviazione del pasto più vecchio fallita, riprovo al prossimo pasto")
        if len(profile["pasti_recenti"]) > profile_ops.MAX_PASTI_RECENTI + 5:
            # Fallimenti ripetuti: tronchiamo comunque, perdendo il riassunto
            # aggiornato ma non lasciando crescere la lista.
            return profile_ops.archivia_pasto_piu_vecchio(profile, profile["riassunto_storico"])
        return profile


def _errore_immagine(immagine_bytes: bytes, media_type: str | None) -> str | None:
    if media_type and media_type not in MEDIA_TYPE_IMMAGINE_SUPPORTATI:
        return (
            "Non riesco a leggere questo tipo di file. Mandami il menu come foto "
            "normale (JPEG o PNG) invece che come file."
        )
    if len(immagine_bytes) > MAX_IMMAGINE_BYTES:
        return (
            "L'immagine è troppo grande per essere analizzata. Rimandamela come "
            "foto a qualità normale, non come file originale."
        )
    return None


def processa_messaggio(
    profile: dict,
    testo: str | None,
    immagine_bytes: bytes | None = None,
    media_type: str | None = None,
) -> tuple[dict, list[str]]:
    """Percorso unico per messaggi e comandi conversazionali.

    `testo` può essere un seme generato da un comando (SEME_START,
    SEME_FEEDBACK) invece che un messaggio scritto dall'utente: per l'agente
    non cambia nulla, ed è così che esiste un solo percorso di esecuzione.
    """
    # Le guardie stanno in testa: valgono prima di qualsiasi chiamata all'API.
    if immagine_bytes is not None:
        errore = _errore_immagine(immagine_bytes, media_type)
        if errore is not None:
            return profile, [errore]

    # Il reset è distruttivo e resta un ramo hard, prima dell'agente.
    if profile.get("in_attesa_di_conferma_reset"):
        if testo is None:
            return profile, ["Rispondi CONFERMA o scrivimi qualcos'altro per annullare il reset."]
        return handle_reset_confirmation(profile, testo)

    testo_foto = None
    if immagine_bytes is not None:
        testo_foto = claude_client.descrivi_immagine(immagine_bytes, media_type or "image/jpeg")

    testo_utente = testo or "(nessun testo, solo una foto)"
    profile = profile_ops.aggiungi_a_cronologia(profile, "utente", testo_utente)

    blocchi = prompts.build_blocchi_utente(profile, testo_utente, testo_foto)
    profile, risposta = claude_client.esegui_agente(
        prompts.SYSTEM_PROMPT_AGENTE, blocchi, profile
    )

    profile = _manutenzione_storico(profile)
    profile = profile_ops.aggiungi_a_cronologia(profile, "bot", risposta)
    return profile, [risposta]
