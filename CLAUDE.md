# mensa-bot

Bot Telegram **mono-utente** che aiuta una persona a scegliere cosa mangiare in
mensa. Il codice, i commenti, i prompt, i messaggi all'utente e i nomi di
funzione sono **in italiano**: mantieni la convenzione.

Vedi `architecture.md` per i diagrammi di flusso.

## Comandi

```bash
uv sync
uv run pytest          # 195 test, devono restare tutti verdi
uv run python bot.py   # richiede le variabili di .env.example
```

Non esiste un linter configurato. Lo stile lo si tiene guardando i file
accanto: righe attorno ai 100 caratteri, niente type checker, niente classi.

## Come è fatto

Package piatto alla radice, nessun `src/`, nessuna classe. Il profilo utente è
un `dict` che attraversa tutto il sistema; ogni modulo lo prende e ne
restituisce una copia aggiornata.

| File | Responsabilità | Regola |
|---|---|---|
| `bot.py` | Wiring python-telegram-bot, polling, I/O Telegram, traduzione degli errori | L'unico che conosce Telegram |
| `handlers.py` | Un turno di conversazione: guardie, cronologia, chiamata all'agente, manutenzione | Non conosce né Telegram né la persistenza |
| `claude_client.py` | Chiamate all'API Anthropic e loop agentico | L'unico che conosce l'SDK `anthropic` |
| `agent_tools.py` | Definizioni dei tool + dispatcher | **Funzione pura**, nessuna I/O, non solleva mai |
| `prompts.py` | System prompt, schemi JSON, costruzione del turno utente | Solo stringhe e dict, nessuna logica |
| `profile_ops.py` | Trasformazioni pure sul profilo | Nessuna I/O di rete, nessun import di `claude_client` |
| `storage.py` | Persistenza su Upstash Redis | L'unico che conosce Redis |

La direzione delle dipendenze è `bot → handlers → claude_client → agent_tools →
profile_ops`, con `prompts` foglia. Non introdurre import all'indietro: se
`profile_ops` avesse bisogno di `claude_client`, il pezzo di logica sta nel
posto sbagliato.

## Il modello decide, il codice esegue

Il bot **è un agente**. Non c'è una macchina a stati che instrada i messaggi:
c'è un system prompt che descrive i passaggi tipici come linee guida, e cinque
tool che il modello sceglie quando servono.

Questo è il punto architetturale da non smontare per sbaglio. Il flusso
deterministico precedente instradava per stati fissi con il ramo "menu" come
default, e per questo leggeva un feedback spontaneo («ho assaggiato una
ortolana e mi è piaciuta molto») come un menu di una riga. Il test in
`tests/test_regressione_feedback_spontaneo.py` presidia quel caso.

**Se un comportamento è sbagliato, il primo posto da guardare è
`SYSTEM_PROMPT_AGENTE` o la `description` di un tool, non un nuovo `if` in
`handlers.py`.** Aggiungere rami deterministici davanti all'agente riporta
indietro il problema che il refactor ha risolto.

Restano deterministici, di proposito:

- chat lock e idempotenza per `update_id` (sicurezza, valgono prima di ogni effetto)
- la conferma di `/reset` (azione distruttiva, e infatti **non esiste un tool
  per azzerare il profilo**: è la difesa strutturale contro le injection)
- `/preferenze` (lettura di dati locali: farla passare dal modello costerebbe
  una chiamata e permetterebbe un riepilogo inventato)
- `/export`, `/sbloccachat`
- l'archiviazione dei pasti oltre i 20 (manutenzione: un suo fallimento non
  deve diventare un errore su cui l'agente si mette a ragionare)

`/start` e `/feedback` **non** sono percorsi separati: sono semi testuali
(`handlers.SEME_START`, `handlers.SEME_FEEDBACK`) passati all'agente come un
messaggio qualsiasi, così esiste un solo percorso di esecuzione da mantenere.

## I tool

Cinque, tutti di **scrittura**: `salva_preferenze`,
`aggiorna_allergie_intolleranze`, `registra_raccomandazione`,
`registra_feedback_pasto`, `segna_onboarding_completato`.

Non ci sono tool di lettura ed è deliberato: il profilo intero (~2,5K token) sta
nel prompt ad ogni turno, quindi un tool di lettura costerebbe un round-trip
garantito senza aggiungere informazione. Se ti serve dare al modello un dato
nuovo, **aggiungilo a `build_blocchi_utente`**, non a un tool.

Aggiungendo un tool:

- l'`input_schema` deve rispettare gli stessi vincoli degli structured output
  (`additionalProperties: false`, ogni proprietà in `required`, `enum` invece di
  `minimum`/`maximum`) perché sono dichiarati con `strict: True`;
- valida comunque l'input in `esegui_tool`: `strict` garantisce la forma dello
  schema, non la sensatezza del contenuto (item vuoto, stringhe di soli spazi);
- restituisci `(profilo, testo, is_error)` e **non sollevare**: un errore che
  torna come `tool_result` permette al modello di correggersi, un'eccezione fa
  saltare il turno;
- riporta nel testo di ritorno anche cosa hai scartato — è la differenza fra un
  modello che si corregge e uno che non sa di aver fallito;
- registra il nome in `_DISPATCH` (un test verifica che ogni tool dichiarato
  abbia un'implementazione).

## Vincoli da non violare

**Allergie e intolleranze.** Sono un vincolo assoluto su tre livelli: la sezione
in cima al system prompt, il blocco `<allergie_vincolo_assoluto>` separato nello
snapshot, e un backstop deterministico in `_registra_raccomandazione` che
rifiuta una scelta il cui nome contiene letteralmente un allergene dichiarato.
Il backstop prende solo le occorrenze letterali (mai "besciamella" per il
lattosio): è una rete in più, non un sostituto del prompt. Ogni modifica
all'elenco viene confermata esplicitamente all'utente in chat — un vincolo di
questo tipo non deve mai cambiare in silenzio.

**Blocchi di thinking.** Nel loop agentico `response.content` va riaccodato
**intero**, blocchi `thinking` compresi: con il thinking attivo devono tornare
intatti insieme ai `tool_use`, altrimenti l'API rifiuta il turno successivo.
`_testo_risposta` li *scarta* ed è il motivo per cui dentro il loop si usa
`_testo_finale`. C'è un test di regressione dedicato.

**Prompt caching.** L'ordine di rendering è `tools` → `system` → `messages`, e
dentro il loop quel prefisso si ripete 3-4 volte. Quindi `SYSTEM_PROMPT_AGENTE`
deve restare **byte-identico** ad ogni iterazione: niente data, niente id,
niente dati di profilo interpolati. Tutto ciò che varia sta nel turno utente,
dopo il breakpoint di cache. Un test verifica che non ci siano `{` nel prompt.

**Input non fidato.** Menu, testo OCR delle foto e cronologia sono scritti da
fonti esterne. Vanno delimitati in blocchi e quotati con `!r`, che li tiene su
una riga sola. Con i tool il rischio non è più solo "consiglio sbagliato" ma
"scrittura sul profilo": non esporre mai tool distruttivi.

**Immagini fuori dal loop.** L'API è stateless: un blocco immagine dentro il
loop verrebbe ri-tokenizzato ad ogni iterazione (~1.500-2.500 token a giro) e ad
ogni turno successivo che lo tenesse in cronologia. `descrivi_immagine` lo
converte in testo **una volta sola**, prima che entri nella conversazione.
Conseguenza: le annotazioni sugli allergeni vanno trascritte in quel passaggio,
perché a valle nessuno vede più la foto.

**Event loop.** Sia il client Anthropic sia `storage` (che usa `requests`) sono
sincroni. Ogni chiamata bloccante passa da `asyncio.to_thread` in `bot.py`:
mantieni la logica sincrona e testabile, non propagare `async` verso il basso.

## Il profilo

Unico blob JSON in Redis alla chiave `mensa_bot:profile`, letto e riscritto per
intero ad ogni messaggio, con backup rotante su 7 chiavi (una per giorno della
settimana). Lo schema è `profile_ops.DEFAULT_PROFILE`.

Tre livelli di memoria: `allergie_intolleranze` (vincoli assoluti), `preferenze`
(gusti con sentiment e peso 1-5, merge last-write-wins su `item`),
`pasti_recenti` (ultimi 20) più `riassunto_storico` che comprime i più vecchi.
In più `cronologia`, finestra scorrevole di ~10 scambi che serve a risolvere i
riferimenti al turno precedente («perfetta», «quella di ieri»).

Cambiando lo schema: `normalizza_profilo` fonde i dati salvati sopra
`DEFAULT_PROFILE` e poi applica una **whitelist delle chiavi**, quindi un campo
rimosso sparisce dal blob al primo salvataggio. Aggiungi sempre una validazione
difensiva per i campi nuovi — un tipo sbagliato in `chat_id` o
`ultimo_update_id` renderebbe il bot muto senza un errore visibile.

La cronologia ha un tetto che va applicato **in scrittura e in lettura**: un
blob manomesso o scritto da una versione precedente gonfierebbe il prompt.

## Modello

`ANTHROPIC_MODEL`, default `claude-sonnet-5`. Deve supportare tool use e
structured outputs: `claude-sonnet-5`, `claude-opus-5`, `claude-opus-4-8`,
`claude-haiku-4-5`. Con modelli più vecchi le chiamate falliscono con un 400.

`effort` resta a `medium` per il loop agentico: con `low` il modello tende a non
chiamare i tool.

## Deploy

Railway, deploy automatico su push a `main`, start command `uv run python bot.py`.
Il servizio non espone porte (polling verso Telegram). Il piano Trial non ha
volumi persistenti: è il motivo per cui il profilo sta su Upstash Redis e non su
disco. Se un giorno ci fossero i volumi, basterebbe cambiare `storage.py`.
