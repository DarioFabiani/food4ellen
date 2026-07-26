"""Wrapper attorno alle chiamate Anthropic API usate dal bot.

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

import anthropic

import agent_tools
import prompts

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_JSON_RETRIES = 1

# Oltre questo numero di giri il modello non sta convergendo: meglio chiudere a
# parole che continuare a chiamare tool.
MAX_ITERAZIONI_AGENTE = 6
MAX_TOKENS = 8192

RISPOSTA_DI_RIPIEGO = (
    "Ho aggiornato quello che dovevo, ma non sono riuscito a metterlo in parole. Riprova a scrivermi."
)

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
            # Con lo schema imposto dal server il modello non sta sbagliando
            # formato: o il budget di token è finito nel thinking (nessun blocco
            # di testo) o il JSON è troncato a metà. In entrambi i casi la
            # correzione è più margine, non un messaggio di correzione.
            max_tokens = min(max_tokens * 2, MAX_TOKENS)
    raise ValueError(
        f"Impossibile ottenere JSON valido dopo {MAX_JSON_RETRIES + 1} tentativi"
    ) from ultimo_errore


# ---------------------------------------------------------------------------
# Loop agentico
# ---------------------------------------------------------------------------


def _blocchi_system(system: str) -> list[dict]:
    """Il system prompt come singolo blocco cacheabile.

    Il breakpoint copre anche le definizioni dei tool, che l'API rende prima
    del system prompt: dentro il loop quel prefisso si ripete ad ogni giro.
    """
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _testo_finale(response) -> str:
    """Concatena tutti i blocchi di testo della risposta, scartando thinking e
    tool_use. A differenza di _testo_risposta non solleva: a questo punto i tool
    sono già stati eseguiti e il profilo va salvato comunque."""
    pezzi = [blocco.text for blocco in response.content if blocco.type == "text"]
    testo = "\n\n".join(pezzo.strip() for pezzo in pezzi if pezzo.strip())
    if not testo:
        logger.warning("Nessun blocco di testo nella risposta finale dell'agente")
        return RISPOSTA_DI_RIPIEGO
    return testo


def _chiama_agente(system: str, messages: list, max_tokens: int, effort: str, tool_choice=None):
    parametri = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": _blocchi_system(system),
        "messages": messages,
        "tools": agent_tools.TOOL_DEFINITIONS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }
    if tool_choice is not None:
        parametri["tool_choice"] = tool_choice
    return _get_client().messages.create(**parametri)


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

        if response.stop_reason == "max_tokens" and not ritentato_per_troncamento:
            # Thinking, testo e input dei tool condividono il budget: se si è
            # esaurito, la correzione è più margine, non un altro prompt.
            ritentato_per_troncamento = True
            max_tokens = min(max_tokens * 2, MAX_TOKENS)
            logger.warning("Risposta troncata, ritento con max_tokens=%d", max_tokens)
            continue

        if response.stop_reason != "tool_use":
            return profile, _testo_finale(response)

        # response.content va rimandato indietro INTERO: con il thinking attivo
        # i blocchi di ragionamento devono tornare intatti insieme ai tool_use,
        # altrimenti il turno successivo viene rifiutato.
        messages.append({"role": "assistant", "content": response.content})

        risultati = []
        for blocco in response.content:
            if blocco.type != "tool_use":
                continue
            profile, testo, is_error = agent_tools.esegui_tool(blocco.name, blocco.input, profile)
            logger.info("Tool %s -> is_error=%s: %s", blocco.name, is_error, testo)
            risultati.append(
                {
                    "type": "tool_result",
                    "tool_use_id": blocco.id,
                    "content": testo,
                    "is_error": is_error,
                }
            )

        # Tutti i risultati in UN solo messaggio: spezzarli insegnerebbe al
        # modello a non chiamare più i tool in parallelo.
        messages.append({"role": "user", "content": risultati})

    # Iterazioni esaurite: un ultimo giro senza tool, così l'utente riceve
    # comunque una risposta invece di restare a mani vuote col profilo già
    # modificato.
    logger.warning("Loop agente esaurito dopo %d iterazioni", MAX_ITERAZIONI_AGENTE)
    response = _chiama_agente(system, messages, max_tokens, effort, tool_choice={"type": "none"})
    return profile, _testo_finale(response)


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
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": image_b64},
        },
        {"type": "text", "text": prompts.build_testo_utente_immagine()},
    ]
    risultato = _call_json(
        prompts.SYSTEM_PROMPT_IMMAGINE,
        content,
        prompts.SCHEMA_IMMAGINE,
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
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=prompts.SYSTEM_PROMPT_SUMMARY_COMPRESSION,
        messages=[{"role": "user", "content": user_prompt}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    )
    return _testo_risposta(response).strip()
