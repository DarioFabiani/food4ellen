# Architettura

Diagrammi del funzionamento di mensa-bot. Per le regole di sviluppo vedi
`CLAUDE.md`.

## 1. Moduli e dipendenze

Package piatto, dipendenze a senso unico. Ogni modulo conosce una cosa sola:
`bot` conosce Telegram, `claude_client` conosce l'SDK Anthropic, `storage`
conosce Redis. `agent_tools` e `profile_ops` sono puri.

```mermaid
flowchart TD
    TG(["Telegram"]) <--> BOT["bot.py<br/><i>polling, I/O, errori</i>"]
    BOT --> ST["storage.py"]
    ST --> RD[("Upstash Redis<br/>mensa_bot:profile")]
    BOT --> H["handlers.py<br/><i>un turno di conversazione</i>"]
    H --> CC["claude_client.py<br/><i>chiamate API e loop</i>"]
    CC --> API(["API Anthropic"])
    CC --> AT["agent_tools.py<br/><i>tool + dispatcher, puro</i>"]
    AT --> PO["profile_ops.py<br/><i>trasformazioni pure</i>"]
    H --> PO
    H -.-> PR["prompts.py<br/><i>stringhe e schemi</i>"]
    CC -.-> PR
    AT -.-> PR

    classDef puro fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef io fill:#e3f2fd,stroke:#1565c0,color:#0d47a1
    classDef est fill:#f5f5f5,stroke:#9e9e9e,color:#424242
    class AT,PO,PR puro
    class BOT,ST,CC io
    class TG,API,RD est
```

Verde = puro e testabile senza mock. Blu = fa I/O. Grigio = esterno.

## 2. Instradamento in `bot.py`

Due filtri validi per tutto, poi lo smistamento. `/start` e `/feedback` non sono
percorsi separati: sostituiscono il testo con un seme e finiscono nello stesso
`processa_messaggio` di un messaggio scritto a mano.

```mermaid
flowchart TD
    IN(["Messaggio o comando"]) --> LOAD["storage.load_profile"]
    LOAD --> CHAT{"Chat<br/>autorizzata?"}
    CHAT -->|no| STOP1(["Ignora in silenzio"])
    CHAT -->|sì| DUP{"update_id<br/>già visto?"}
    DUP -->|sì| STOP2(["Ignora: replay del polling"])
    DUP -->|no| CMD{"Che tipo<br/>di input?"}

    CMD -->|"/preferenze"| PREF["Riepilogo deterministico"]
    CMD -->|"/export<br/>/sbloccachat"| SERV["Comandi di servizio"]
    CMD -->|"/reset"| ARMA["Arma la conferma"]
    CMD -->|"/start<br/>/feedback"| SEME["Testo = seme"]
    CMD -->|"testo o foto"| PROC["processa_messaggio<br/><i>diagramma 3</i>"]
    SEME --> PROC

    PREF --> SAVE["storage.save_profile<br/><i>+ backup del giorno</i>"]
    SERV --> SAVE
    ARMA --> SAVE
    PROC --> SAVE
    SAVE --> OUT(["Risposta su Telegram"])

    classDef det fill:#fff3e0,stroke:#e65100,color:#e65100
    classDef llm fill:#ede7f6,stroke:#4527a0,color:#311b92
    class PREF,SERV,ARMA det
    class PROC llm
```

Arancione = deterministico di proposito. Viola = passa dal modello.

`/preferenze` è arancione perché è una lettura di dati locali: farla passare dal
modello costerebbe una chiamata e permetterebbe un riepilogo inventato.

## 3. Un turno di conversazione

Il corpo di `handlers.processa_messaggio`. Tutto ciò che sta prima dell'agente
sono guardie: nessuna di esse interpreta il contenuto del messaggio.

```mermaid
flowchart TD
    PROC(["processa_messaggio"]) --> GUARD{"Immagine<br/>valida?"}
    GUARD -->|"formato o peso KO"| ERR(["Messaggio d'errore"])
    GUARD -->|"ok, o nessuna immagine"| RESET{"Conferma reset<br/>armata?"}
    RESET -->|sì| CONF["handle_reset_confirmation<br/><i>ramo hard: azione distruttiva</i>"]
    RESET -->|no| FOTO{"C'è una<br/>foto?"}

    FOTO -->|sì| VIS["descrivi_immagine<br/><i>una chiamata, fuori dal loop</i>"]
    FOTO -->|no| CRON["Registra il turno utente<br/>in cronologia"]
    VIS --> CRON
    CRON --> BLOCCHI["build_blocchi_utente:<br/>snapshot del profilo<br/>+ messaggio di adesso"]
    BLOCCHI --> LOOP["esegui_agente<br/><i>diagramma 4</i>"]
    LOOP --> MANU["Archivia il pasto più vecchio<br/>se sono oltre 20"]
    MANU --> CRON2["Registra la risposta<br/>in cronologia"]
    CRON2 --> OUT(["profilo aggiornato, risposta"])
    CONF --> OUT

    classDef det fill:#fff3e0,stroke:#e65100,color:#e65100
    classDef llm fill:#ede7f6,stroke:#4527a0,color:#311b92
    class CONF,MANU det
    class VIS,LOOP llm
```

L'archiviazione è arancione e sta **fuori** dai tool di proposito: è
manutenzione interna, e un suo fallimento non deve diventare un errore su cui
l'agente si mette a ragionare. Se fallisce, la risposta all'utente parte lo
stesso.

## 4. Il loop agentico

Il cuore del sistema. Il modello riceve lo stato e decide se scrivere qualcosa
nel profilo prima di rispondere.

```mermaid
sequenceDiagram
    participant H as handlers
    participant C as claude_client
    participant A as API Anthropic
    participant T as agent_tools
    participant P as profilo

    H->>C: esegui_agente(system, blocchi, profilo)
    C->>C: messages = [turno utente]

    loop max 6 iterazioni
        C->>A: create(tools, thinking, system cacheabile)
        A-->>C: risposta

        alt stop_reason = max_tokens
            Note over C: raddoppia max_tokens<br/>una volta sola, poi si arrende
        else stop_reason = tool_use
            C->>C: accoda response.content INTERO<br/>(thinking compresi)
            loop per ogni blocco tool_use
                C->>T: esegui_tool(nome, argomenti, profilo)
                T->>P: merge / record / apply
                T-->>C: (profilo', testo, is_error)
            end
            C->>C: tutti i tool_result in UN messaggio user
        else altro stop_reason
            Note over C: esce dal loop
        end
    end

    alt iterazioni esaurite
        C->>A: create(tool_choice = none)
        Note over C,A: chiude a parole invece di lasciare<br/>l'utente senza risposta
    end

    C->>C: concatena i blocchi di testo
    Note over C: se non ce n'è, risposta di ripiego:<br/>il profilo è già stato modificato
    C-->>H: (profilo aggiornato, testo)
```

Due dettagli che sembrano marginali e non lo sono:

- `response.content` torna indietro **intero**. Con il thinking attivo i blocchi
  di ragionamento devono accompagnare i `tool_use`, altrimenti l'API rifiuta il
  turno successivo con un 400.
- tutti i `tool_result` vanno in **un solo** messaggio user. Spezzarli insegna
  al modello a smettere di chiamare i tool in parallelo.

## 5. Cosa può decidere il modello

Non c'è routing per intento: il modello riceve lo stesso contesto in ogni caso e
sceglie. La colonna dei tool è l'unico modo che ha di modificare il profilo.

```mermaid
flowchart LR
    M(["Messaggio<br/>dell'utente"]) --> AG{{"Agente"}}

    AG -->|"non so ancora nulla di te"| ON["Fa una domanda<br/>sui gusti"]
    AG -->|"questo è il menu di oggi"| RC["Sceglie un'opzione<br/>e la motiva"]
    AG -->|"sta commentando un pasto"| FB["Registra com'è andata"]
    AG -->|"ha detto un gusto di sfuggita"| PR["Se lo segna"]
    AG -->|"ha dichiarato un'intolleranza"| AL["Aggiorna i vincoli"]
    AG -->|"sta solo chiacchierando"| CH["Risponde e basta"]

    ON -.-> T1["salva_preferenze"]
    ON -.-> T5["segna_onboarding_completato"]
    RC -.-> T3["registra_raccomandazione"]
    FB -.-> T4["registra_feedback_pasto"]
    FB -.-> T1
    PR -.-> T1
    AL -.-> T2["aggiorna_allergie_intolleranze"]

    T1 --> DB[("profilo")]
    T2 --> DB
    T3 --> DB
    T4 --> DB
    T5 --> DB

    classDef tool fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    class T1,T2,T3,T4,T5 tool
```

I casi non sono mutuamente esclusivi ed è il punto: «buonissima, ma un po'
salata» produce insieme un `registra_feedback_pasto` e un `salva_preferenze`,
nella stessa risposta. Era esattamente il caso che il flusso deterministico
sbagliava, trattandolo come un menu.

**Non esiste un tool che cancella.** Il reset passa da una conferma testuale in
`handlers`, fuori dalla portata del modello: è la difesa strutturale contro un
menu o una foto che provi a farsi passare per istruzione.

## 6. Memoria

Quattro strutture nello stesso blob JSON, con orizzonti diversi.

```mermaid
flowchart TD
    subgraph blob["mensa_bot:profile — un unico JSON su Redis"]
        AL["<b>allergie_intolleranze</b><br/>vincolo assoluto, permanente"]
        PR["<b>preferenze</b><br/>gusti con sentiment e peso 1-5<br/>merge per 'item', last write wins"]
        PA["<b>pasti_recenti</b><br/>ultimi 20, con id e gradimento"]
        RS["<b>riassunto_storico</b><br/>5-6 righe di pattern a lungo termine"]
        CR["<b>cronologia</b><br/>ultimi ~10 scambi, 500 caratteri l'uno"]
    end

    PA -->|"oltre i 20, il più vecchio"| COMP["update_riassunto_storico<br/><i>chiamata di manutenzione</i>"]
    COMP --> RS

    blob --> SNAP["build_blocchi_utente"]
    SNAP --> CTX(["Turno utente:<br/>ogni chiamata riparte da qui"])

    classDef vinc fill:#ffebee,stroke:#c62828,color:#b71c1c
    class AL vinc
```

Il profilo viene letto e riscritto **per intero** ad ogni messaggio, con backup
rotante su sette chiavi, una per giorno della settimana.

La `cronologia` non è uno storico: è una finestra che serve a risolvere i
riferimenti al turno precedente. Salva testo renderizzato e non turni `messages`
reali, perché i turni assistant passati contenevano blocchi `tool_use` che non
persistiamo — e l'API pretende che ogni `tool_use` sia seguito dal suo
`tool_result`.

## 7. Dove finiscono i token

Ordine di rendering della richiesta, e cosa si ripete dentro il loop.

```mermaid
flowchart LR
    subgraph stabile["Prefisso stabile — cacheabile"]
        direction TB
        TO["definizioni dei 5 tool"] --> SY["SYSTEM_PROMPT_AGENTE<br/><i>byte-identico ad ogni giro</i>"]
    end
    SY --> BP{{"cache_control<br/>ephemeral"}}
    BP --> VAR
    subgraph VAR["Parte variabile — ripagata ad ogni iterazione"]
        direction TB
        SN["snapshot del profilo<br/>~2,5K token"] --> CO["cronologia"] --> FO["testo della foto, se c'è"] --> MS["messaggio di adesso"]
    end
    VAR --> TR["turni di tool_use e tool_result<br/><i>crescono ad ogni giro</i>"]
```

Da qui discendono due vincoli:

- **il system prompt non può contenere dati variabili** — niente data, niente
  id, niente profilo interpolato: cambierebbe ad ogni iterazione e il caching
  non aggancerebbe;
- **le immagini non entrano nel loop.** L'API è stateless, quindi un blocco
  immagine costerebbe 1.500-2.500 token ad ogni giro e ad ogni turno successivo
  che lo tenesse in cronologia. `descrivi_immagine` lo converte in testo una
  volta sola: le stesse informazioni scendono a 50-100 token. Il prezzo è che le
  annotazioni sugli allergeni vanno trascritte in quel passaggio, perché a valle
  nessuno vede più la foto.
