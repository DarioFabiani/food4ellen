"""Wrapper attorno alle chiamate Anthropic API usate dal bot."""
from __future__ import annotations

import base64
import json
import logging
import os

import anthropic

import prompts

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_JSON_RETRIES = 2

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _strip_code_fences(testo: str) -> str:
    testo = testo.strip()
    if testo.startswith("```"):
        testo = testo.split("\n", 1)[1] if "\n" in testo else testo
        if testo.endswith("```"):
            testo = testo.rsplit("```", 1)[0]
    return testo.strip()


def _call_json(system: str, content, max_tokens: int = 1024) -> dict:
    messages = [{"role": "user", "content": content}]
    ultimo_errore = None
    for tentativo in range(MAX_JSON_RETRIES + 1):
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        testo = response.content[0].text
        try:
            return json.loads(_strip_code_fences(testo))
        except json.JSONDecodeError as exc:
            ultimo_errore = exc
            logger.warning("JSON non valido dal modello (tentativo %d): %s", tentativo + 1, testo)
            messages.append({"role": "assistant", "content": testo})
            messages.append(
                {
                    "role": "user",
                    "content": "La risposta precedente non era JSON valido. Rispondi SOLO con JSON valido, nessun altro testo.",
                }
            )
    raise ValueError(
        f"Impossibile ottenere JSON valido dopo {MAX_JSON_RETRIES + 1} tentativi"
    ) from ultimo_errore


def parse_onboarding_answer(step: int, risposta_utente: str) -> dict:
    user_prompt = prompts.build_onboarding_user_prompt(step, risposta_utente)
    risultato = _call_json(prompts.SYSTEM_PROMPT_ONBOARDING_PARSING, user_prompt)
    logger.info("Onboarding step %d parsato: %s", step, risultato)
    return risultato


def extract_menu_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> list[str]:
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": image_b64},
        },
        {"type": "text", "text": prompts.build_menu_vision_user_text()},
    ]
    risultato = _call_json(prompts.SYSTEM_PROMPT_MENU_VISION, content)
    return risultato.get("opzioni_menu", [])


def get_recommendation(
    opzioni_menu: list[str],
    allergie_intolleranze: list[str],
    preferenze: list[dict],
    pasti_recenti: list[dict],
    riassunto_storico: str,
) -> dict:
    user_prompt = prompts.build_recommendation_user_prompt(
        opzioni_menu, allergie_intolleranze, preferenze, pasti_recenti, riassunto_storico
    )
    risultato = _call_json(prompts.SYSTEM_PROMPT_RECOMMENDATION, user_prompt)
    logger.info("Raccomandazione: %s", risultato)
    return risultato


def parse_feedback(pasto: dict, preferenze_attuali: list[dict], feedback_testo: str) -> dict:
    user_prompt = prompts.build_feedback_user_prompt(pasto, preferenze_attuali, feedback_testo)
    risultato = _call_json(prompts.SYSTEM_PROMPT_FEEDBACK_PARSING, user_prompt)
    logger.info("Feedback parsato per pasto %s: %s", pasto["id"], risultato)
    return risultato


def update_riassunto_storico(riassunto_attuale: str, pasto_da_archiviare: dict) -> str:
    user_prompt = prompts.build_summary_update_prompt(riassunto_attuale, pasto_da_archiviare)
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=512,
        system=prompts.SYSTEM_PROMPT_SUMMARY_COMPRESSION,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()
