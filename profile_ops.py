"""Logica pura di manipolazione del profilo (nessuna I/O di rete)."""
from __future__ import annotations

import copy
import uuid
from datetime import date

MAX_PASTI_RECENTI = 20


def merge_preferenze(profile: dict, nuove_preferenze: list[dict]) -> dict:
    profile = copy.deepcopy(profile)
    esistenti = {p["item"]: p for p in profile["preferenze"]}
    for nuova in nuove_preferenze:
        esistenti[nuova["item"]] = nuova
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
