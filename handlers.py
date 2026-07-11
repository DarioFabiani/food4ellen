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


def handle_feedback_command(profile: dict) -> tuple[dict, list[str]]:
    pasto = profile_ops.find_pasto_in_attesa_di_feedback(profile)
    if pasto is None:
        return profile, ["Non ho pasti in attesa di feedback al momento."]
    profile = copy.deepcopy(profile)
    profile["in_attesa_di_feedback_per"] = pasto["id"]
    return profile, [f"Com'è andata con '{pasto['scelta_consigliata']}'?"]


def handle_feedback_answer(profile: dict, feedback_testo: str) -> tuple[dict, list[str]]:
    pasto_id = profile.get("in_attesa_di_feedback_per")
    pasto = next((p for p in profile["pasti_recenti"] if p["id"] == pasto_id), None)
    if pasto is None:
        return profile, ["Non so a quale pasto si riferisce, usa prima /feedback."]

    estratto = claude_client.parse_feedback(pasto, profile["preferenze"], feedback_testo)
    profile = profile_ops.apply_feedback(
        profile,
        pasto_id,
        estratto["gradimento"],
        estratto.get("scelta_reale"),
        feedback_testo,
        estratto.get("nuove_preferenze", []),
    )
    profile = copy.deepcopy(profile)
    profile["in_attesa_di_feedback_per"] = None
    return profile, ["Grazie, ho aggiornato le tue preferenze!"]


def handle_preferenze_command(profile: dict) -> tuple[dict, list[str]]:
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
    import storage

    if risposta_utente.strip().upper() != "CONFERMA":
        profile = copy.deepcopy(profile)
        profile["in_attesa_di_conferma_reset"] = False
        return profile, ["Reset annullato."]

    nuovo_profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    nuovo_profilo["chat_id"] = profile.get("chat_id")
    return nuovo_profilo, ["Profilo azzerato. Ricominciamo dall'onboarding!", messaggio_domanda_onboarding(1)]
