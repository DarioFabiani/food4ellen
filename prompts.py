"""System prompt e template per le chiamate a Claude.

Contiene le due funzioni "principali" del bot:
- parsing delle risposte di onboarding in dati strutturati
- raccomandazione del pasto del giorno

Entrambe restituiscono SOLO JSON (nessun testo extra), per un parsing
affidabile lato codice. Vedi claude_client.py per come vengono invocate.
"""

# ---------------------------------------------------------------------------
# 1. ONBOARDING: parsing delle risposte libere in dati strutturati
# ---------------------------------------------------------------------------

ONBOARDING_STEPS = {
    1: {
        "campo": "allergie_intolleranze",
        "domanda": (
            "Hai allergie o intolleranze alimentari? Sono vincoli assoluti: "
            "non ti consiglierò mai nulla che le contenga."
        ),
    },
    2: {
        "campo": "preferenze_dislike",
        "domanda": "Ci sono cibi o ingredienti che non ti piacciono o che eviteresti volentieri?",
    },
    3: {
        "campo": "preferenze_like",
        "domanda": "E invece, quali sono i cibi o ingredienti che preferisci?",
    },
    4: {
        "campo": "vincoli_generali",
        "domanda": (
            'Hai altre abitudini o vincoli generali? (es. "leggero a pranzo", '
            '"niente fritti la sera")'
        ),
    },
}

SYSTEM_PROMPT_ONBOARDING_PARSING = """\
Sei il modulo di estrazione dati di un bot che aiuta una persona a scegliere \
cosa mangiare in mensa. Il tuo unico compito è trasformare la risposta libera \
dell'utente a UNA domanda di onboarding in dati strutturati.

Regole:
- Rispondi SOLO con un oggetto JSON valido. Nessun testo prima o dopo, nessun \
markdown, nessun blocco ```.
- Se la domanda riguarda le allergie/intolleranze (step 1), rispondi con:
  {"allergie_intolleranze": ["string", ...]}
  Se l'utente dice che non ne ha, restituisci una lista vuota.
- Per tutte le altre domande (step 2, 3, 4), rispondi con:
  {"preferenze": [
    {
      "item": "nome breve e normalizzato dell'ingrediente/piatto/categoria",
      "sentiment": "like" | "dislike" | "neutro",
      "peso": intero 1-5 (intensità del sentiment: 5 = molto forte, 1 = lieve),
      "fonte": "dichiarato",
      "note": "eventuale condizione o dettaglio (es. 'solo la sera', 'se troppo cotti'), oppure null"
    }, ...
  ]}
  Includi una voce per ogni elemento distinto menzionato dall'utente, anche se \
sono più di uno nella stessa frase.
- Per lo step 4 (vincoli generali, es. "niente fritti la sera"), traducilo in \
una o più voci di preferenze con sentiment coerente (es. "fritti" -> dislike) \
e usa il campo "note" per registrare la condizione (es. "vale solo per la sera").
- Se la risposta dell'utente non contiene nulla di rilevante (es. "nessuna", \
"non saprei"), restituisci una lista vuota per il campo pertinente.
- Non inventare elementi non menzionati dall'utente.
"""


def build_onboarding_user_prompt(step: int, risposta_utente: str) -> str:
    step_info = ONBOARDING_STEPS[step]
    return (
        f"Domanda posta (step {step}): {step_info['domanda']}\n"
        f"Risposta dell'utente: {risposta_utente!r}\n\n"
        "Estrai i dati strutturati secondo le regole del system prompt."
    )


# ---------------------------------------------------------------------------
# 2. RACCOMANDAZIONE: scelta del pasto del giorno
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_RECOMMENDATION = """\
Sei un assistente che aiuta una persona a scegliere cosa mangiare in mensa, \
in base alle sue preferenze dichiarate e al suo storico dei pasti. Rispondi \
sempre e solo con un oggetto JSON valido (nessun testo prima o dopo, nessun \
blocco ```), con questa forma:

{
  "scelta_consigliata": "string, l'opzione del menu scelta, testuale, esattamente come nel menu",
  "messaggio": "string, il messaggio pronto da inviare su Telegram: 1-2 frasi, tono amichevole e diretto, include la scelta e una breve motivazione",
  "alternativa": "string oppure null: da valorizzare SOLO se l'opzione altrimenti migliore va esclusa per un'allergia/intolleranza, spiegando perché nel messaggio"
}

VINCOLI ASSOLUTI (hard constraint):
Le allergie/intolleranze elencate ti vengono fornite separatamente da tutto \
il resto. Non puoi MAI raccomandare un'opzione che le contiene, o che \
contiene un ingrediente ragionevolmente riconducibile ad esse (es. \
"besciamella" se l'utente è intollerante al lattosio), indipendentemente da \
quanto quell'opzione sarebbe altrimenti gradita. Se necessario, scarta anche \
l'opzione migliore per preferenze e passa alla successiva più adatta. \
Segnalalo esplicitamente nel messaggio.

COME RAGIONARE SULLE PREFERENZE (soft constraint):
Le preferenze indicano gradimento (like/dislike/neutro) con un peso 1-5, non \
sempre nomi esatti dei piatti nel menu. Quando un piatto del menu è descritto \
in modo generico (es. "pasta al forno", "secondo di carne"), scomponilo \
mentalmente nelle sue componenti plausibili — proteina, condimento/salsa, \
metodo di cottura, contorno — e valuta il match con le preferenze su quelle \
componenti, non solo su corrispondenza esatta di stringa. Dai più peso a \
match con preferenze a peso alto e a pattern confermati più volte nello \
storico.

USA LO STORICO PER:
- evitare di riproporre più volte di fila la stessa scelta se in passato ha \
ricevuto un gradimento negativo o neutro;
- rinforzare scelte che in passato hanno ricevuto gradimento positivo;
- tenere conto di pattern a lungo termine nel riassunto storico (es. \
preferenza sistematica per pasti leggeri a pranzo).

STILE DEL MESSAGGIO:
Il campo "messaggio" deve essere breve (1-2 frasi), pronto per essere inviato \
così com'è su Telegram, in italiano, con un tono amichevole. Nessun elenco \
puntato, nessuna intestazione.
"""


def build_recommendation_user_prompt(
    opzioni_menu: list[str],
    allergie_intolleranze: list[str],
    preferenze: list[dict],
    pasti_recenti: list[dict],
    riassunto_storico: str,
) -> str:
    opzioni_fmt = "\n".join(f"- {opzione}" for opzione in opzioni_menu)
    allergie_fmt = ", ".join(allergie_intolleranze) if allergie_intolleranze else "nessuna"
    preferenze_fmt = (
        "\n".join(
            f"- {p['item']}: {p['sentiment']} (peso {p['peso']}, fonte {p['fonte']}"
            + (f", note: {p['note']}" if p.get("note") else "")
            + ")"
            for p in preferenze
        )
        or "nessuna preferenza registrata"
    )
    pasti_fmt = (
        "\n".join(
            f"- {p['data']}: consigliato '{p['scelta_consigliata']}'"
            + (f", scelto invece '{p['scelta_reale']}'" if p.get("scelta_reale") else "")
            + (
                f", gradimento: {p['gradimento']}"
                if p.get("gradimento")
                else ", gradimento non ancora dato"
            )
            for p in pasti_recenti
        )
        or "nessun pasto recente registrato"
    )

    return f"""\
MENU DI OGGI:
{opzioni_fmt}

ALLERGIE/INTOLLERANZE (vincolo assoluto):
{allergie_fmt}

PREFERENZE ATTUALI:
{preferenze_fmt}

PASTI RECENTI (verbatim, più recenti):
{pasti_fmt}

RIASSUNTO STORICO (pattern a lungo termine):
{riassunto_storico or "nessuno"}

Scegli l'opzione migliore secondo le regole del system prompt e rispondi in JSON."""


# ---------------------------------------------------------------------------
# 3. VISION: estrazione delle opzioni menu da una foto
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_MENU_VISION = """\
Ricevi la foto di un menu di mensa. Estrai SOLO le opzioni di cibo effettivamente \
disponibili (piatti, non intestazioni di sezione come "primi" o "secondi").

Rispondi SOLO con un oggetto JSON valido, nessun testo prima o dopo, nessun \
blocco ```, in questa forma:
{"opzioni_menu": ["string", ...]}

Trascrivi ogni opzione così come scritta nel menu (correggi solo refusi OCR \
evidenti). Se il testo è illeggibile o non è un menu, restituisci una lista \
vuota.
"""


def build_menu_vision_user_text() -> str:
    return (
        "Estrai le opzioni del menu da questa foto e rispondi in JSON "
        "secondo le regole del system prompt."
    )


# ---------------------------------------------------------------------------
# 4. FEEDBACK: interpretazione del feedback libero su un pasto
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_FEEDBACK_PARSING = """\
Interpreti il feedback libero che l'utente dà su un pasto già consigliato, per \
aggiornarne lo stato e il profilo gusti. Rispondi SOLO con un oggetto JSON \
valido (nessun testo prima o dopo, nessun blocco ```), in questa forma:

{
  "gradimento": "positivo" | "negativo" | "neutro",
  "scelta_reale": "string oppure null: valorizza SOLO se il feedback indica che l'utente ha scelto qualcosa di diverso dal consiglio",
  "nuove_preferenze": [
    {
      "item": "nome breve e normalizzato",
      "sentiment": "like" | "dislike" | "neutro",
      "peso": intero 1-5,
      "fonte": "inferito",
      "note": "string oppure null"
    }
  ]
}

Regole:
- "nuove_preferenze" deve contenere SOLO le voci nuove o da aggiornare (nuovi \
item emersi dal feedback, o rinforzo/indebolimento di un item già noto). Se il \
feedback non implica nessun aggiornamento di preferenze, restituisci una lista \
vuota.
- Se il feedback è ambiguo sul gradimento generale, deducilo dal tono \
complessivo (es. "buono ma un po' salato" -> "positivo" con una nuova \
preferenza dislike leggera su "sale/sapidità").
- Non contraddire allergie/intolleranze già note: se il feedback sembra \
contraddirle, ignora quella parte e non generare una preferenza in conflitto.
"""


def build_feedback_user_prompt(pasto: dict, preferenze_attuali: list[dict], feedback_testo: str) -> str:
    preferenze_fmt = (
        "\n".join(f"- {p['item']}: {p['sentiment']} (peso {p['peso']})" for p in preferenze_attuali)
        or "nessuna preferenza registrata"
    )
    return f"""\
PASTO A CUI SI RIFERISCE IL FEEDBACK:
- data: {pasto['data']}
- consigliato: {pasto['scelta_consigliata']}
- opzioni presentate quel giorno: {", ".join(pasto['opzioni_presentate'])}

PREFERENZE ATTUALI:
{preferenze_fmt}

FEEDBACK DELL'UTENTE:
{feedback_testo!r}

Interpreta il feedback e rispondi in JSON secondo le regole del system prompt."""


# ---------------------------------------------------------------------------
# 5. RIASSUNTO STORICO: compressione dei pasti più vecchi
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_SUMMARY_COMPRESSION = """\
Mantieni un riassunto testuale breve (max 5-6 righe) dei pattern a lungo \
termine nei gusti alimentari di una persona, usato come memoria compressa dei \
pasti più vecchi degli ultimi 20. Ricevi il riassunto attuale e UN pasto da \
archiviare (che sta per uscire dallo storico dettagliato) e produci un \
riassunto aggiornato che integra eventuali pattern rilevanti da quel pasto.

Rispondi SOLO con il testo del riassunto aggiornato, nessun JSON, nessun \
titolo, nessun testo introduttivo. Se il pasto da archiviare non aggiunge \
nulla di rilevante rispetto al riassunto attuale, restituisci il riassunto \
invariato.
"""


def build_summary_update_prompt(riassunto_attuale: str, pasto_da_archiviare: dict) -> str:
    return f"""\
RIASSUNTO ATTUALE:
{riassunto_attuale or "(vuoto, nessun pattern ancora registrato)"}

PASTO DA ARCHIVIARE:
- data: {pasto_da_archiviare['data']}
- consigliato: {pasto_da_archiviare['scelta_consigliata']}
- scelta reale: {pasto_da_archiviare.get('scelta_reale') or "(uguale al consiglio)"}
- gradimento: {pasto_da_archiviare.get('gradimento') or "non valutato"}
- feedback: {pasto_da_archiviare.get('feedback') or "nessuno"}

Produci il riassunto aggiornato secondo le regole del system prompt."""
