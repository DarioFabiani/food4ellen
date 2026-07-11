# mensa-bot

Bot Telegram che raccomanda cosa scegliere in mensa in base a preferenze e storico pasti.

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
| `ANTHROPIC_MODEL` | Modello da usare (default `claude-sonnet-5`) |
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

- `/start` — avvia l'onboarding se non completato, altrimenti messaggio di benvenuto
- `/feedback` — chiede com'è andato l'ultimo pasto non ancora valutato
- `/preferenze` — mostra un riepilogo delle preferenze correnti
- `/reset` — azzera il profilo (richiede conferma esplicita scrivendo `CONFERMA`)
