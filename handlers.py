"""Logica di business per ogni comando/messaggio del bot.

Le funzioni qui non toccano né Telegram né la persistenza: ricevono il
profilo corrente e l'input dell'utente, chiamano claude_client quando serve,
e restituiscono (profilo_aggiornato, messaggi_da_inviare). bot.py chiama
queste funzioni e si occupa di leggere/scrivere il profilo e di parlare con
Telegram.
"""
from __future__ import annotations

import copy

import claude_client
import profile_ops
from prompts import ONBOARDING_STEPS

TOTALE_STEP_ONBOARDING = 4


def messaggio_domanda_onboarding(step: int) -> str:
    return ONBOARDING_STEPS[step]["domanda"]


def handle_start(profile: dict) -> tuple[dict, list[str]]:
    if profile["onboarding_completato"]:
        return profile, ["Bentornata! Mandami il menu di oggi (testo o foto) quando vuoi un consiglio."]
    messaggi = [
        "Ciao! Prima di iniziare ti faccio quattro domande veloci per capire i tuoi gusti.",
        messaggio_domanda_onboarding(profile["onboarding_step"]),
    ]
    return profile, messaggi


def handle_onboarding_answer(profile: dict, risposta_utente: str) -> tuple[dict, list[str]]:
    step = profile["onboarding_step"]
    estratto = claude_client.parse_onboarding_answer(step, risposta_utente)
    profile = copy.deepcopy(profile)

    if step == 1:
        profile["allergie_intolleranze"] = estratto.get("allergie_intolleranze", [])
    else:
        profile = profile_ops.merge_preferenze(profile, estratto.get("preferenze", []))

    if step >= TOTALE_STEP_ONBOARDING:
        profile["onboarding_completato"] = True
        return profile, [
            "Perfetto, ho registrato le tue preferenze! Da ora in poi mandami il "
            "menu del giorno (anche una foto) e ti dirò cosa scegliere."
        ]

    profile["onboarding_step"] = step + 1
    return profile, [messaggio_domanda_onboarding(profile["onboarding_step"])]


def handle_menu(profile: dict, opzioni_menu: list[str]) -> tuple[dict, list[str]]:
    if not opzioni_menu:
        return profile, ["Non sono riuscito a leggere delle opzioni di menu da questo messaggio, puoi riprovare?"]

    raccomandazione = claude_client.get_recommendation(
        opzioni_menu,
        profile["allergie_intolleranze"],
        profile["preferenze"],
        profile["pasti_recenti"],
        profile["riassunto_storico"],
    )
    profile = profile_ops.record_new_meal(profile, opzioni_menu, raccomandazione["scelta_consigliata"])

    da_archiviare = profile_ops.pasto_piu_vecchio_da_archiviare(profile)
    if da_archiviare is not None:
        nuovo_riassunto = claude_client.update_riassunto_storico(profile["riassunto_storico"], da_archiviare)
        profile = profile_ops.archivia_pasto_piu_vecchio(profile, nuovo_riassunto)

    return profile, [raccomandazione["messaggio"]]
