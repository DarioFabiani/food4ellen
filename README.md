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

📐 [`architecture.md`](architecture.md) — diagrammi di moduli, flusso dei
messaggi, loop agentico e memoria.
🤖 [`CLAUDE.md`](CLAUDE.md) — convenzioni e vincoli per chi ci mette mano.

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
| `ANTHROPIC_API_KEY` | Chiave API Anthropic |
| `ANTHROPIC_MODEL` | Modello da usare (default `claude-sonnet-5`). Deve supportare **tool use** e **structured outputs**: `claude-sonnet-5`, `claude-opus-5`, `claude-opus-4-8`, `claude-haiku-4-5`. Con modelli più vecchi le chiamate falliscono con un errore 400 |
| `UPSTASH_REDIS_REST_URL` | URL REST del database Upstash Redis |
| `UPSTASH_REDIS_REST_TOKEN` | Token REST del database Upstash Redis |
| `ALLOWED_CHAT_ID` | (opzionale) chat_id a cui limitare il bot; se assente, il bot si "aggancia" al primo chat_id che gli scrive e lo salva nel profilo |

## Persistenza

Il profilo (preferenze, storico pasti, ecc.) non viene salvato su disco: il
piano Railway usato (Trial) non supporta volumi persistenti, quindi verrebbe
perso ad ogni redeploy/restart. Viene invece salvato come JSON in un'unica
chiave su **Upstash Redis** (piano gratuito, persistente):

1. Crea un database Redis gratuito su https://console.upstash.com
2. Copia "REST URL" e "REST TOKEN" dalla dashboard del database
3. Impostali come `UPSTASH_REDIS_REST_URL` e `UPSTASH_REDIS_REST_TOKEN`

Se in futuro passi a un piano Railway con volumi persistenti, si può tornare
a un file `profile.json` su disco modificando solo `storage.py` — il resto
del bot non dipende dal meccanismo di persistenza.

### Backup e ripristino

Ad ogni salvataggio il profilo viene scritto anche su una **chiave di backup
rotante** `mensa_bot:profile:backup:<giorno>`, dove `<giorno>` è il giorno
della settimana (`0` = lunedì ... `6` = domenica). Si conservano quindi gli
ultimi 7 giorni di storia, uno per giorno. Un fallimento della scrittura di
backup viene solo loggato e non fa fallire il salvataggio principale.

Per ripristinare da un backup, dalla console Upstash (scheda "Data Browser" o
"CLI"):

```
GET mensa_bot:profile:backup:2      # controlla il contenuto del giorno voluto
SET mensa_bot:profile "<il JSON copiato dal comando sopra>"
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
- `/sbloccachat` — libera il `chat_id` registrato nel profilo: il prossimo che
  scrive al bot viene registrato come proprietario. Se `ALLOWED_CHAT_ID` è
  impostata, quella variabile ha comunque la precedenza

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
