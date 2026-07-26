"""Wrapper attorno alle chiamate LLM usate dal bot, via LiteLLM.

LiteLLM parla con qualunque provider (Anthropic, OpenAI, OpenRouter, modelli
locali...) con un'unica interfaccia in stile OpenAI: basta cambiare `LLM_MODEL`
(prefisso `<provider>/<modello>`) e la chiave API del provider scelto. Le
feature Anthropic-specifiche che vogliamo comunque sfruttare quando il
provider attivo è Claude (prompt caching via `cache_control`) sono isolate
in `_is_anthropic` e attivate solo in quel caso: con un altro provider quei
blocchi semplicemente non vengono aggiunti, senza bisogno di rami separati
nel resto del codice.

Tre chiamate distinte:
- `esegui_agente`, il loop con i tool che gestisce la conversazione;
- `descrivi_immagine`, one-shot con structured output, per convertire una foto
  in testo prima che entri nella conversazione;
- `update_riassunto_storico`, one-shot di manutenzione sullo storico pasti.
"""
from __future__ import annotations

import base64
import json
import logging
import os

import litellm

import agent_tools
import prompts

logger = logging.getLogger(__name__)


def _con_provider_di_default(model: str) -> str:
    """Un model id senza prefisso provider (uso storico) resta su Anthropic."""
    return model if "/" in model else f"anthropic/{model}"


MODEL = _con_provider_di_default(
    os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
)
MAX_JSON_RETRIES = 1

# Oltre questo numero di giri il modello non sta convergendo: meglio chiudere a
# parole che continuare a chiamare tool.
MAX_ITERAZIONI_AGENTE = 6
MAX_TOKENS = 8192

RISPOSTA_DI_RIPIEGO = (
    "Ho aggiornato quello che dovevo, ma non sono riuscito a metterlo in parole. Riprova a scrivermi."
)


def _is_anthropic(model: str = MODEL) -> bool:
    return model.startswith("anthropic/")


def _completion(**kwargs):
    return litellm.completion(**kwargs)


def _testo_risposta(message) -> str:
    testo = getattr(message, "content", None)
    if not testo:
        raise ValueError("Nessun blocco di testo nella risposta del modello")
    return testo


def _call_json(
    system: str,
    content,
    schema: dict,
    schema_name: str = "output",
    max_tokens: int = 2048,
    effort: str = "low",
) -> dict:
    """Chiamata con structured output: lo schema garantisce la forma di una
    risposta *completa*, ma una risposta troncata (finish_reason="length") può
    comunque non contenere testo o contenere JSON incompleto: da qui il retry."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": True},
    }
    ultimo_errore = None
    for tentativo in range(MAX_JSON_RETRIES + 1):
        response = _completion(
            model=MODEL,
            max_tokens=max_tokens,
            messages=messages,
            response_format=response_format,
            reasoning_effort=effort,
        )
        message = response.choices[0].message
        testo = None
        try:
            testo = _testo_risposta(message)
            return json.loads(testo)
        except (ValueError, json.JSONDecodeError) as exc:
            ultimo_errore = exc
            logger.warning(
                "Risposta non utilizzabile (tentativo %d, finish_reason=%s): %r",
                tentativo + 1, response.choices[0].finish_reason, testo,
            )
            # Il budget di token è finito nel reasoning (nessun blocco di testo)
            # o il JSON è troncato a metà. In entrambi i casi la correzione è
            # più margine, non un messaggio di correzione.
            max_tokens = min(max_tokens * 2, MAX_TOKENS)
    raise ValueError(
        f"Impossibile ottenere JSON valido dopo {MAX_JSON_RETRIES + 1} tentativi"
    ) from ultimo_errore


# ---------------------------------------------------------------------------
# Loop agentico
# ---------------------------------------------------------------------------


def _messaggio_system(system: str) -> dict:
    """Il system prompt come messaggio, con breakpoint di cache solo su Claude.

    Il breakpoint copre anche le definizioni dei tool (vedi `_tools_openai`),
    che l'API rende prima del system prompt: dentro il loop quel prefisso si
    ripete ad ogni giro.
    """
    if _is_anthropic():
        contenuto = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    else:
        contenuto = system
    return {"role": "system", "content": contenuto}


def _tools_openai(definizioni: list[dict]) -> list[dict]:
    """Le definizioni tool (in forma nativa Anthropic) tradotte in tool spec
    OpenAI-style: è il formato che LiteLLM traduce per ogni provider."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
                "strict": tool.get("strict", False),
            },
        }
        for tool in definizioni
    ]
    if _is_anthropic() and tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _messaggio_assistant(message) -> dict:
    """Il messaggio assistant da rimandare indietro intatto al giro successivo.

    Con modelli che ragionano (Claude incluso) il contenuto di reasoning va
    rimandato insieme ai tool_calls, altrimenti il turno successivo viene
    rifiutato o perde continuità.
    """
    messaggio = {"role": "assistant", "content": getattr(message, "content", None)}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        messaggio["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
    reasoning_content = getattr(message, "reasoning_content", None)
    if reasoning_content:
        messaggio["reasoning_content"] = reasoning_content
    return messaggio


def _testo_finale(message) -> str:
    testo = (getattr(message, "content", None) or "").strip()
    if not testo:
        logger.warning("Nessun blocco di testo nella risposta finale dell'agente")
        return RISPOSTA_DI_RIPIEGO
    return testo


def _chiama_agente(system: str, messages: list, max_tokens: int, effort: str, tool_choice=None):
    parametri = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [_messaggio_system(system), *messages],
        "tools": _tools_openai(agent_tools.TOOL_DEFINITIONS),
        "reasoning_effort": effort,
    }
    if tool_choice is not None:
        parametri["tool_choice"] = tool_choice
    return _completion(**parametri)


def esegui_agente(
    system: str,
    blocchi_utente,
    profile: dict,
    max_tokens: int = 4096,
    effort: str = "medium",
) -> tuple[dict, str]:
    """Esegue un turno di conversazione con i tool, applicandoli al profilo.

    Restituisce (profilo_aggiornato, testo_da_inviare).
    """
    messages: list = [{"role": "user", "content": blocchi_utente}]
    ritentato_per_troncamento = False

    for _ in range(MAX_ITERAZIONI_AGENTE):
        response = _chiama_agente(system, messages, max_tokens, effort)
        scelta = response.choices[0]

        if scelta.finish_reason == "length" and not ritentato_per_troncamento:
            # Reasoning, testo e input dei tool condividono il budget: se si è
            # esaurito, la correzione è più margine, non un altro prompt.
            ritentato_per_troncamento = True
            max_tokens = min(max_tokens * 2, MAX_TOKENS)
            logger.warning("Risposta troncata, ritento con max_tokens=%d", max_tokens)
            continue

        if scelta.finish_reason != "tool_calls":
            return profile, _testo_finale(scelta.message)

        messages.append(_messaggio_assistant(scelta.message))

        # Un messaggio "tool" per ogni tool_call_id: è la convenzione OpenAI-
        # style, a differenza del content-block-in-un-solo-messaggio di
        # Anthropic — qui non serve raggrupparli, il formato lo fa già capire
        # al modello che erano chiamate parallele.
        for tc in scelta.message.tool_calls:
            argomenti = json.loads(tc.function.arguments or "{}")
            profile, testo, is_error = agent_tools.esegui_tool(tc.function.name, argomenti, profile)
            logger.info("Tool %s -> is_error=%s: %s", tc.function.name, is_error, testo)
            prefisso = "ERRORE: " if is_error else ""
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": prefisso + testo})

    # Iterazioni esaurite: un ultimo giro senza tool, così l'utente riceve
    # comunque una risposta invece di restare a mani vuote col profilo già
    # modificato.
    logger.warning("Loop agente esaurito dopo %d iterazioni", MAX_ITERAZIONI_AGENTE)
    response = _chiama_agente(system, messages, max_tokens, effort, tool_choice="none")
    return profile, _testo_finale(response.choices[0].message)


# ---------------------------------------------------------------------------
# Chiamate one-shot
# ---------------------------------------------------------------------------


def descrivi_immagine(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """Converte una foto in testo per l'agente.

    La conversione avviene una volta sola, fuori dal loop: l'API è stateless,
    quindi un blocco immagine dentro il loop verrebbe ri-tokenizzato ad ogni
    iterazione e ad ogni turno successivo che lo tenesse in cronologia.
    """
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    content = [
        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
        {"type": "text", "text": prompts.build_testo_utente_immagine()},
    ]
    risultato = _call_json(
        prompts.SYSTEM_PROMPT_IMMAGINE,
        content,
        prompts.SCHEMA_IMMAGINE,
        schema_name="foto_menu",
        max_tokens=4096,
        effort="medium",
    )
    logger.info("Foto letta: tipo=%s, %d opzioni", risultato.get("tipo"), len(risultato.get("opzioni_menu") or []))
    return prompts.formatta_foto(
        risultato.get("tipo", "altro"),
        risultato.get("opzioni_menu") or [],
        risultato.get("descrizione", ""),
    )


def update_riassunto_storico(riassunto_attuale: str, pasto_da_archiviare: dict) -> str:
    user_prompt = prompts.build_summary_update_prompt(riassunto_attuale, pasto_da_archiviare)
    response = _completion(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": prompts.SYSTEM_PROMPT_SUMMARY_COMPRESSION},
            {"role": "user", "content": user_prompt},
        ],
        reasoning_effort="low",
    )
    return _testo_risposta(response.choices[0].message).strip()
