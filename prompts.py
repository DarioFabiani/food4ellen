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
