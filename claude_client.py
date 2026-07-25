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
MAX_JSON_RETRIES = 1

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _testo_risposta(response) -> str:
    """Estrae il blocco di testo dalla risposta, scartando eventuali blocchi di
    thinking che il modello può anteporre al testo vero e proprio."""
    for blocco in response.content:
        if blocco.type == "text":
            return blocco.text
    raise ValueError("Nessun blocco di testo nella risposta del modello")


def _call_json(system: str, content, schema: dict, max_tokens: int = 2048, effort: str = "low") -> dict:
    """Chiamata con structured output: lo schema garantisce la forma di una
    risposta *completa*, ma una risposta troncata (stop_reason max_tokens) può
    comunque non contenere testo o contenere JSON incompleto: da qui il retry."""
    messages = [{"role": "user", "content": content}]
    ultimo_errore = None
    for tentativo in range(MAX_JSON_RETRIES + 1):
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort},
        )
        testo = None
        try:
            testo = _testo_risposta(response)
            return json.loads(testo)
        except (ValueError, json.JSONDecodeError) as exc:
            ultimo_errore = exc
            logger.warning(
                "Risposta non utilizzabile (tentativo %d, stop_reason=%s): %r",
                tentativo + 1, getattr(response, "stop_reason", None), testo,
            )
            if testo is None:
                # Nessun blocco di testo: tipicamente il budget di token è
                # finito nel thinking. Non c'è nulla da rimandare al modello.
                max_tokens = min(max_tokens * 2, 8192)
                continue
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
    # Lo step 1 raccoglie allergie/intolleranze, gli altri le preferenze
    # (stessa logica di handlers.handle_onboarding_answer).
    schema = prompts.SCHEMA_ONBOARDING_ALLERGIE if step == 1 else prompts.SCHEMA_ONBOARDING_PREFERENZE
    risultato = _call_json(prompts.SYSTEM_PROMPT_ONBOARDING_PARSING, user_prompt, schema)
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
    risultato = _call_json(
        prompts.SYSTEM_PROMPT_MENU_VISION,
        content,
        prompts.SCHEMA_MENU_VISION,
        max_tokens=4096,
        effort="medium",
    )
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
    risultato = _call_json(
        prompts.SYSTEM_PROMPT_RECOMMENDATION,
        user_prompt,
        prompts.SCHEMA_RECOMMENDATION,
        effort="medium",
    )
    logger.info("Raccomandazione: %s", risultato)
    return risultato


def parse_feedback(pasto: dict, preferenze_attuali: list[dict], feedback_testo: str) -> dict:
    user_prompt = prompts.build_feedback_user_prompt(pasto, preferenze_attuali, feedback_testo)
    risultato = _call_json(
        prompts.SYSTEM_PROMPT_FEEDBACK_PARSING, user_prompt, prompts.SCHEMA_FEEDBACK
    )
    logger.info("Feedback parsato per pasto %s: %s", pasto["id"], risultato)
    return risultato


def update_riassunto_storico(riassunto_attuale: str, pasto_da_archiviare: dict) -> str:
    user_prompt = prompts.build_summary_update_prompt(riassunto_attuale, pasto_da_archiviare)
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=prompts.SYSTEM_PROMPT_SUMMARY_COMPRESSION,
        messages=[{"role": "user", "content": user_prompt}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    )
    return _testo_risposta(response).strip()
