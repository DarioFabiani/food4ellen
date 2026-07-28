# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Cos'è

Bot Telegram (in italiano) che raccomanda cosa scegliere in mensa in base a
preferenze e storico pasti dell'utente. È un **agente a tool**, non un flusso
deterministico: il system prompt in `prompts.py` descrive i passaggi tipici
(raccolta gusti → menu → consiglio → feedback) come linee guida, e il modello
decide di volta in volta cosa fare — comandi come `/start` o `/feedback` sono
solo semi di testo passati allo stesso percorso di un messaggio scritto a
mano, non percorsi separati.

## Comandi

```bash
uv sync                 # installa le dipendenze (usa uv, non pip/poetry)
cp .env.example .env     # poi compila le variabili (vedi README.md)
uv run pytest            # intera suite
uv run pytest tests/test_agent_tools.py                       # un file
uv run pytest tests/test_agent_tools.py::test_nome_del_test   # un singolo test
uv run pytest -k "reset"                                      # per pattern sul nome
uv run python bot.py     # avvia il bot in polling
```

Non c'è un linter/formatter configurato nel repo.

## Architettura

Flusso di un messaggio, `bot.py` → `handlers.py` → `claude_client.py` / `agent_tools.py`:

1. **`bot.py`** — entry point, wiring di `python-telegram-bot` (polling, non
   webhook). Ogni handler carica il profilo (`storage.load_profile`),
   applica i due filtri comuni (`_chat_autorizzata`, dedup su
   `update.update_id` per evitare doppio processing dei retry di Telegram),
   e infine salva (`storage.save_profile`) e risponde. Il client LLM è
   sincrono e va sempre eseguito con `asyncio.to_thread` per non bloccare il
   polling.
2. **`handlers.py`** — logica di business pura (nessun I/O Telegram/rete
   diretto): riceve profilo + input, restituisce `(profilo_aggiornato,
   messaggi_da_inviare)`. `processa_messaggio` è il percorso unico che
   delega all'agente; restano deterministici solo i rami dove una decisione
   del modello non aggiungerebbe nulla o sarebbe pericolosa (conferma del
   reset, riepilogo preferenze, manutenzione dello storico).
3. **`claude_client.py`** — chiamate LLM via **LiteLLM** (multiprovider,
   interfaccia stile OpenAI). Tre chiamate distinte: `esegui_agente` (loop
   con tool), `descrivi_immagine` (one-shot, structured output, converte una
   foto-menu in testo *prima* di entrare nel loop — l'immagine non rientra
   mai nei turni successivi perché l'API è stateless), `update_riassunto_storico`
   (manutenzione dello storico). Il **prompt caching** Anthropic
   (`cache_control`) è isolato dietro `_is_anthropic()` e attivo solo con
   quel provider.
4. **`agent_tools.py`** — i tool esposti al modello, tutti di *scrittura*
   sul profilo (lo stato che l'agente deve leggere gli arriva già nel
   prompt, quindi un tool di lettura costerebbe un round-trip inutile ad
   ogni messaggio). `esegui_tool` non solleva mai: un errore torna al
   modello come tool_result così può correggersi da solo.
5. **`profile_ops.py`** — logica pura di manipolazione del profilo (nessuna
   I/O). Definisce lo schema (`DEFAULT_PROFILE`) e la normalizzazione
   (`normalizza_profilo`, chiamata ad ogni lettura da storage): un campo
   rimosso dallo schema o corrotto nel blob salvato non deve mai rompere il
   bot.
6. **`storage.py`** — persistenza su **Upstash Redis** via REST API, **una
   chiave per chat** (`mensa_bot:profile:<chat_id>`), perché il bot è
   multi-utente: ogni chat ha profilo/storico isolato. C'è un backup
   rotante su 7 chiavi (una per giorno della settimana, anch'esso per-chat)
   e una migrazione una tantum (`_migra_profilo_legacy`) dalla vecchia
   chiave fissa mono-utente, eseguita in automatico al primo caricamento
   per chat che non ha ancora una chiave propria.
7. **`prompts.py`** — system prompt e schema JSON (preferenze, output della
   lettura immagine). Le annotazioni sugli allergeni nelle foto-menu si
   trascrivono modificando `SYSTEM_PROMPT_IMMAGINE`.

### Multi-provider LLM

Tutte le chiamate passano da LiteLLM: cambiare provider è solo questione di
`LLM_MODEL` (prefisso `<provider>/<modello>`, es.
`openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`,
`anthropic/claude-sonnet-5`) + la chiave API corrispondente. Il modello
scelto deve supportare **tool use** e **structured output**
(`response_format` con `json_schema`) — molti modelli free dichiarano
supporto senza rispettarlo davvero, va verificato dal vivo prima di
cambiarlo (vedi README.md per i dettagli sui test fatti sul default).

### Controllo accessi multi-utente

`ALLOWED_CHAT_IDS` (lista di `chat_id` separati da virgola) decide chi può
usare il bot; se assente il bot è aperto a chiunque scriva, ognuno con
profilo isolato. `ALLOWED_CHAT_ID` (nome pre-refactor, singolare) resta
letta come fallback in `bot._chat_autorizzata`.

### Memoria del profilo

Tre livelli nello stesso blob JSON: `allergie_intolleranze` (vincolo
assoluto, ogni modifica va confermata esplicitamente in chat, mai in
silenzio), `preferenze` (sentiment + peso 1-5, registrate anche di
sfuggita), `pasti_recenti` (ultimi 20) + `riassunto_storico` che comprime i
più vecchi. C'è inoltre una `cronologia` degli ultimi ~10 scambi per
risolvere riferimenti al turno precedente ("perfetta", "quella di ieri") —
è una finestra scorrevole, non uno storico completo.

## Convenzioni del codice

- Commenti e nomi di funzioni/variabili in italiano; i commenti spiegano
  sempre il *perché* di una scelta non ovvia, mai il *cosa* fa il codice.
- Le funzioni di logica pura (`profile_ops.py`, `agent_tools.py`) non fanno
  mai I/O e non sollevano mai eccezioni sul percorso "atteso": errori e
  dati scartati tornano come valori di ritorno, non come exception.
- I test in `conftest.py` scriptano le risposte finte del modello con la
  stessa forma degli oggetti restituiti da LiteLLM (attributi via
  `SimpleNamespace`, non dict) — usa quegli helper (`risposta_testo`,
  `risposta_tool_use`, `risposta_troncata`, `risposta_vuota`) invece di
  costruire mock ad-hoc quando scrivi test che coinvolgono `claude_client`.
