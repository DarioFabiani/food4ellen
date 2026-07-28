# mensa-bot

Bot Telegram che raccomanda cosa scegliere in mensa in base a preferenze e storico pasti.

Il bot è un **agente**: non segue un percorso fisso, ma decide di volta in volta
cosa fare in base a quello che gli scrivi. Il system prompt gli descrive i
passaggi tipici (raccolta iniziale dei gusti → menu → consiglio → feedback) come
linee guida, mentre le azioni che modificano il profilo sono tool che il modello
sceglie quando servono. Vedi `agent_tools.py` per l'elenco.

In pratica: puoi mandargli il menu, raccontargli com'è andato un pasto,
dichiarare una nuova intolleranza o fargli una domanda, in qualsiasi ordine e
senza comandi. Se dici «buonissima, ma un po' salata» dopo un consiglio, lui
capisce che è un feedback e se lo segna — non lo scambia per un nuovo menu.

## Sviluppo locale

```bash
uv sync
cp .env.example .env  # poi compila le variabili
uv run pytest
uv run python bot.py
```

## Variabili d'ambiente

| Variabile | Descrizione |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot, ottenuto da @BotFather |
| `LLM_MODEL` | Modello da usare, con prefisso provider (default `openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`). Deve supportare **tool use** e **structured output** (`response_format` con `json_schema`) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / ... | Chiave del provider scelto in `LLM_MODEL` — solo quella serve, le altre restano vuote |
| `UPSTASH_REDIS_REST_URL` | URL REST del database Upstash Redis |
| `UPSTASH_REDIS_REST_TOKEN` | Token REST del database Upstash Redis |
| `ALLOWED_CHAT_IDS` | (opzionale) lista di `chat_id` ammessi a usare il bot, separati da virgola (es. `111111,222222`); se assente il bot risponde a chiunque scriva, ognuno con il proprio profilo — sconsigliato se più di una persona può trovare il bot. Se impostata solo la vecchia `ALLOWED_CHAT_ID` (nome pre-refactor), viene comunque letta come fallback |

Per sapere il proprio `chat_id`: scrivi al bot una volta (verrà ignorato se non sei ancora in lista) e controlla i log di Railway, che loggano il `chat_id` di ogni chat non autorizzata; oppure chiedilo a un bot terzo come @userinfobot su Telegram.

Il profilo (allergie, preferenze, storico) è isolato per persona, ma la
chiave API del provider LLM è condivisa da tutto il bot: i limiti di
richieste (in particolare quelli, più stretti, dei modelli gratuiti su
OpenRouter) valgono per l'istanza intera, non per persona. Con più utenti
attivi contemporaneamente quel tetto si esaurisce prima.

## Provider e modelli

Le chiamate al modello passano tutte da [LiteLLM](https://docs.litellm.ai/),
che parla con qualunque provider (Anthropic, OpenAI, OpenRouter, modelli
locali...) con un'unica interfaccia. Cambiare provider è solo una questione di
`LLM_MODEL` + la chiave API giusta:

```bash
LLM_MODEL=openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free  # default — richiede OPENROUTER_API_KEY
LLM_MODEL=anthropic/claude-sonnet-5      # richiede ANTHROPIC_API_KEY
LLM_MODEL=openai/gpt-5.5                 # richiede OPENAI_API_KEY
```

Il default è stato scelto testando dal vivo (conversazione, tool-calling,
structured output) diversi modelli free su OpenRouter: molti dichiarano
supporto tool/schema nel catalogo ma non lo rispettano davvero (risposte con
"tool call" scritte in prosa invece che tramite l'API, schema JSON ignorato).
`nemotron-3-nano-omni-30b-a3b-reasoning:free` è il primo a passare tutti e tre
i test — ma quei test coprivano solo testo: la combinazione **immagine +
schema rigido insieme**, che è esattamente cosa fa `descrivi_immagine` per
leggere le foto-menu (comprese le annotazioni sugli allergeni), non è mai
stata verificata dal vivo con questo modello. Se in produzione la lettura
delle foto risulta inaffidabile, il primo sospetto è lì.

Un comportamento resta specializzato per Anthropic (in `claude_client.py`,
dietro `_is_anthropic`): il **prompt caching** (`cache_control`) su system
prompt e definizioni tool, che non ha un equivalente uniforme cross-provider —
con un altro provider quei blocchi restano semplicemente inutilizzati.

## Persistenza

Il profilo (preferenze, storico pasti, ecc.) non viene salvato su disco: il
piano Railway usato (Trial) non supporta volumi persistenti, quindi verrebbe
perso ad ogni redeploy/restart. Viene invece salvato come JSON su **Upstash
Redis** (piano gratuito, persistente), **una chiave per chat**
(`mensa_bot:profile:<chat_id>`): ogni persona che scrive al bot ha il proprio
profilo isolato, senza vedere allergie/preferenze/storico delle altre.

1. Crea un database Redis gratuito su https://console.upstash.com
2. Copia "REST URL" e "REST TOKEN" dalla dashboard del database
3. Impostali come `UPSTASH_REDIS_REST_URL` e `UPSTASH_REDIS_REST_TOKEN`

Se in futuro passi a un piano Railway con volumi persistenti, si può tornare
a file `profile.json` su disco modificando solo `storage.py` — il resto del
bot non dipende dal meccanismo di persistenza.

Il bot era originariamente mono-utente, con un unico profilo su una chiave
fissa (`mensa_bot:profile`). Al primo avvio dopo l'aggiornamento, quel
profilo viene migrato automaticamente sotto la chiave per-chat del suo
proprietario (riconosciuto dal `chat_id` già salvato al suo interno) e la
vecchia chiave viene eliminata: non serve nessuna azione manuale.

### Backup e ripristino

Ad ogni salvataggio il profilo viene scritto anche su una **chiave di backup
rotante per chat** `mensa_bot:profile:backup:<chat_id>:<giorno>`, dove
`<giorno>` è il giorno della settimana (`0` = lunedì ... `6` = domenica). Si
conservano quindi gli ultimi 7 giorni di storia per ogni persona, uno per
giorno. Un fallimento della scrittura di backup viene solo loggato e non fa
fallire il salvataggio principale.

Per ripristinare da un backup, dalla console Upstash (scheda "Data Browser" o
"CLI"):

```
GET mensa_bot:profile:backup:<chat_id>:2      # controlla il contenuto del giorno voluto
SET mensa_bot:profile:<chat_id> "<il JSON copiato dal comando sopra>"
```

Il comando `/export` invia il profilo corrente come file JSON in chat: usalo
per tenerne una copia fuori da Upstash prima di operazioni rischiose.

## Deploy su Railway

1. Collega il repo GitHub al progetto Railway (deploy automatico su push a `main`).
2. Nelle impostazioni del servizio, imposta come **Start Command**: `uv run python bot.py`
   (Railway/Nixpacks rileva `pyproject.toml` + `uv.lock` e fa il build con `uv sync`;
   se il rilevamento fallisse, `requirements.txt` è presente come fallback per un
   build Nixpacks classico basato su `pip install -r requirements.txt`).
3. Configura le variabili d'ambiente elencate sopra nel pannello "Variables".
4. Il servizio non espone porte HTTP (il bot fa polling verso Telegram): non serve
   dominio/HTTPS, ma verifica nelle impostazioni di rete che Railway non richieda
   un health check su una porta (in caso, disattivalo per questo servizio).

## Comandi bot

I comandi sono scorciatoie, non percorsi separati: `/start` e `/feedback`
passano dall'agente esattamente come un messaggio scritto a mano, quindi si può
ottenere lo stesso risultato parlandogli normalmente.

- `/start` — presentazione, e avvio della raccolta iniziale dei gusti se manca
- `/feedback` — chiede com'è andato l'ultimo pasto non ancora valutato
- `/preferenze` — riepilogo delle preferenze correnti (lettura locale, non passa dal modello)
- `/reset` — azzera il profilo (richiede conferma esplicita scrivendo `CONFERMA`)
- `/export` — invia il profilo completo come file JSON (backup manuale)

## Come mandare il menu

Il menu si può mandare come **testo** (una opzione per riga funziona bene, ma
non è obbligatorio) oppure come **immagine**. L'immagine va bene sia come foto
normale sia come file/documento, purché il formato sia JPEG, PNG, GIF o WebP: i
**HEIC/HEIF** di iPhone non sono supportati, in quel caso mandala come foto
normale (Telegram la converte in JPEG). Le immagini oltre ~3,5 MB vengono
rifiutate: rimandale a qualità normale invece che come file originale.

Le foto vengono convertite in testo con una chiamata dedicata **prima** di
entrare nella conversazione, e l'immagine non viene mai rimandata al modello nei
turni successivi: l'API è stateless, quindi tenerla in circolo la farebbe pagare
di nuovo ad ogni scambio. Una conseguenza è che le annotazioni sugli allergeni
vanno trascritte in quel passaggio — se ne perdi qualcuna, il posto in cui
intervenire è `SYSTEM_PROMPT_IMMAGINE` in `prompts.py`.

Se la foto non è un menu (per esempio è il piatto che hai appena mangiato) il
bot la riconosce come tale e la usa come contesto per il feedback.

## Memoria

Il profilo contiene tre livelli di memoria, tutti nello stesso blob JSON:

- `allergie_intolleranze` — vincoli assoluti, aggiornabili in qualsiasi momento
  dichiarandoli in chat. Ogni modifica viene confermata esplicitamente in chat:
  un vincolo di questo tipo non deve mai cambiare in silenzio.
- `preferenze` — gusti con sentiment e peso 1-5. Vengono registrati ogni volta
  che emergono, anche di sfuggita in un messaggio che parla d'altro.
- `pasti_recenti` (ultimi 20) più `riassunto_storico`, che comprime i più
  vecchi in poche righe di pattern a lungo termine.

C'è inoltre una `cronologia` degli ultimi ~10 scambi, che serve a risolvere i
riferimenti al turno precedente («perfetta», «quella di ieri»). Non è uno
storico completo: è una finestra scorrevole, troncata sia in scrittura sia in
lettura.
