# Test

`uv run pytest` dalla radice. `pytest-asyncio` in `asyncio_mode = auto`: i test
async non hanno bisogno del decoratore.

Test in italiano come il resto del progetto, nomi che descrivono il
**comportamento atteso**, non la funzione chiamata:
`test_registra_raccomandazione_blocca_un_opzione_che_contiene_un_allergene`, non
`test_registra_raccomandazione_2`. Quando un test presidia una decisione di
design non ovvia, spiega il perché in una docstring di una riga — serve a chi
un giorno vorrà "semplificare" proprio quel pezzo.

## Dove mockare

La regola è mockare al confine più esterno che rende il test significativo.

| Cosa testi | Cosa mocki |
|---|---|
| `agent_tools.esegui_tool` | **Niente.** È puro: profilo in, profilo fuori |
| `profile_ops` | **Niente.** Trasformazioni pure |
| `prompts` | **Niente.** Solo stringhe |
| `handlers.processa_messaggio` | `handlers.claude_client.esegui_agente` e `descrivi_immagine` |
| `claude_client.esegui_agente` | `claude_client._get_client`, con una sequenza di risposte scriptate |
| `bot.*` | `bot.storage.load_profile` / `save_profile` e `bot.handlers.processa_messaggio` |

Non mockare `agent_tools` nei test di `claude_client`: il valore di quei test è
proprio verificare che il loop applichi davvero i tool al profilo.

## Scriptare il modello

Gli helper stanno in `conftest.py` alla radice e costruiscono oggetti con la
stessa forma di quelli dell'SDK (attributi, non chiavi):

```python
client = MagicMock()
client.messages.create.side_effect = [
    risposta_tool_use("salva_preferenze", {"preferenze": [...]}),
    risposta_testo("Segnato!"),
]

with patch.object(claude_client, "_get_client", return_value=client):
    profilo, testo = claude_client.esegui_agente(SYSTEM, "adoro le zucchine", profilo)
```

`risposta(...)` accetta più blocchi e uno `stop_reason` esplicito, per i casi
con tool paralleli, thinking o troncamento. Per ispezionare cosa è stato
rimandato al modello: `client.messages.create.call_args_list[N].kwargs`.

## Cosa non deve regredire

Alcuni test presidiano decisioni che sembrano dettagli e non lo sono. Se ne
rompi uno, leggi la docstring prima di aggiustare l'asserzione:

- **thinking rimandati inalterati** (`test_claude_client.py`) — rimuoverli fa
  fallire il turno successivo con un 400;
- **tool paralleli in un solo messaggio user** — spezzarli insegna al modello a
  smettere di chiamare i tool in parallelo;
- **risposta finale senza testo** — il profilo va salvato lo stesso, i tool sono
  già stati eseguiti;
- **backstop allergie** — deve bloccare l'occorrenza letterale ma non scattare
  su una sottostringa di un'altra parola ("noci" dentro "nocino");
- **system prompt senza dati variabili** (`test_prompts.py`) — se cambia ad ogni
  iterazione il prompt caching non aggancia;
- **whitelist delle chiavi del profilo** (`test_profile_ops.py`) — senza, un
  campo rimosso resta nel blob Redis per sempre;
- **`test_regressione_feedback_spontaneo.py`** — il caso che ha motivato tutto
  il refactor: un commento su un pasto non deve mai diventare un menu.

## Profili di test

`_profilo_base(**overrides)` in ogni file, costruito con `copy.deepcopy` di
`storage.DEFAULT_PROFILE`. Usa la deepcopy e non `{**DEFAULT_PROFILE}`: lo
shallow copy condivide le liste con il default e un test può inquinarne un
altro.

Quando aggiungi un campo al profilo, controlla `CHIAVI_USATE_DA_HANDLERS` in
`test_profile_ops.py`.

## Cosa non è coperto

Nessun test tocca la rete: non c'è verifica che l'API accetti davvero gli
schemi dei tool o che il caching agganci. Quelle vanno provate con
`uv run python bot.py` e credenziali vere (fai `/export` prima, il profilo su
Redis è quello di produzione), controllando `usage.cache_read_input_tokens` nei
log per il caching.
