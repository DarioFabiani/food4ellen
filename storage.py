"""Persistenza del profilo utente su Upstash Redis (via REST API).

Il piano Railway Trial non supporta volumi persistenti, quindi il profilo
non vive su disco: viene serializzato come stringa JSON e salvato in
un'unica chiave Redis.
"""
from __future__ import annotations

import copy
import json
import os

import requests

PROFILE_KEY = "mensa_bot:profile"

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
}


def _base_url() -> str:
    return os.environ["UPSTASH_REDIS_REST_URL"].rstrip("/")


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['UPSTASH_REDIS_REST_TOKEN']}"}


def load_profile() -> dict:
    resp = requests.get(f"{_base_url()}/get/{PROFILE_KEY}", headers=_headers(), timeout=10)
    resp.raise_for_status()
    result = resp.json().get("result")
    if result is None:
        profile = copy.deepcopy(DEFAULT_PROFILE)
        save_profile(profile)
        return profile
    return json.loads(result)


def save_profile(profile: dict) -> None:
    payload = json.dumps(profile)
    resp = requests.post(
        f"{_base_url()}/set/{PROFILE_KEY}",
        headers=_headers(),
        data=payload.encode("utf-8"),
        timeout=10,
    )
    resp.raise_for_status()
