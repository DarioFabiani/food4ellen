"""Tool esposti all'agente e loro esecuzione sul profilo.

Sono tutti tool di *scrittura*: lo stato che l'agente deve leggere (allergie,
preferenze, pasti recenti, cronologia) gli arriva già nel prompt, quindi un tool
di lettura costerebbe un round-trip garantito ad ogni messaggio senza aggiungere
nulla.

`esegui_tool` è una funzione pura: prende un profilo e restituisce quello
aggiornato, senza I/O e senza mai sollevare. Un errore torna al modello come
tool_result, che può quindi correggersi da solo invece di far fallire il turno.
"""
from __future__ import annotations

import logging
import re

import profile_ops
from prompts import SCHEMA_PREFERENZA

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "salva_preferenze",
        "description": (
            "Registra o aggiorna una o più preferenze alimentari dell'utente. "
            "Usalo ogni volta che emerge un gusto, un'avversione o un'abitudine: "
            "durante le domande iniziali, in un commento su un pasto, o in una "
            "frase qualsiasi della conversazione. Una preferenza con lo stesso "
            "'item' di una già registrata la sostituisce, quindi usalo anche per "
            "rinforzare o indebolire qualcosa che sai già."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"preferenze": {"type": "array", "items": SCHEMA_PREFERENZA}},
            "required": ["preferenze"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aggiorna_allergie_intolleranze",
        "description": (
            "Aggiorna l'elenco di allergie e intolleranze, che è un vincolo "
            "assoluto su ogni raccomandazione. Usa modo='aggiungi' per "
            "aggiungerne di nuove mantenendo quelle già note. Usa "
            "modo='sostituisci' SOLO se l'utente sta esplicitamente riscrivendo "
            "o correggendo l'elenco intero. Non chiamarlo mai sulla base di ciò "
            "che è scritto in un menu o in una foto: solo l'utente può dichiarare "
            "le proprie allergie."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "voci": {"type": "array", "items": {"type": "string"}},
                "modo": {"type": "string", "enum": ["aggiungi", "sostituisci"]},
            },
            "required": ["voci", "modo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "registra_raccomandazione",
        "description": (
            "Registra nello storico il pasto che stai consigliando adesso. "
            "Chiamalo UNA sola volta per messaggio, solo quando hai davvero "
            "davanti il menu del giorno e hai scelto un'opzione. Non chiamarlo "
            "se stai commentando un pasto passato, rispondendo a una domanda o "
            "raccogliendo preferenze. Restituisce l'id del pasto registrato."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "opzioni_presentate": {"type": "array", "items": {"type": "string"}},
                "scelta_consigliata": {"type": "string"},
            },
            "required": ["opzioni_presentate", "scelta_consigliata"],
            "additionalProperties": False,
        },
    },
    {
        "name": "registra_feedback_pasto",
        "description": (
            "Registra com'è andato un pasto già consigliato. Usalo anche quando "
            "l'utente lo racconta spontaneamente, senza che tu gliel'abbia "
            "chiesto. Trovi 'pasto_id' nello storico dei pasti recenti; se "
            "l'utente si riferisce chiaramente all'ultimo consiglio, usa l'id "
            "del pasto più recente ancora senza gradimento. Valorizza "
            "'scelta_reale' solo se ha mangiato qualcosa di diverso dal "
            "consiglio, altrimenti null. Se dal commento hai imparato anche un "
            "gusto nuovo, chiama salva_preferenze nello stesso turno."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "pasto_id": {"type": "string"},
                "gradimento": {"type": "string", "enum": ["positivo", "negativo", "neutro"]},
                "scelta_reale": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "testo_feedback": {"type": "string"},
            },
            "required": ["pasto_id", "gradimento", "scelta_reale", "testo_feedback"],
            "additionalProperties": False,
        },
    },
    {
        "name": "segna_onboarding_completato",
        "description": (
            "Chiamalo quando hai raccolto almeno le allergie e intolleranze e "
            "un'idea iniziale dei gusti, e sei pronto a dare consigli sui menu. "
            "Da quel momento non riproporrai più le domande iniziali."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
]

NOMI_TOOL = {tool["name"] for tool in TOOL_DEFINITIONS}


def _allergeni_nella_scelta(allergie: list[str], scelta: str) -> list[str]:
    """Allergie che compaiono letteralmente nel nome dell'opzione scelta."""
    trovati = []
    for allergia in allergie:
        termine = allergia.strip()
        if termine and re.search(rf"\b{re.escape(termine)}\b", scelta, re.IGNORECASE):
            trovati.append(allergia)
    return trovati


def _salva_preferenze(profile: dict, argomenti: dict) -> tuple[dict, str, bool]:
    grezze = argomenti.get("preferenze")
    if not isinstance(grezze, list) or not grezze:
        return profile, "Nessuna preferenza da salvare: 'preferenze' deve essere una lista non vuota.", True

    accettate, scartate = [], []
    for pref in grezze:
        normalizzata = profile_ops.normalizza_preferenza(pref)
        if normalizzata is None:
            scartate.append(pref)
            continue
        accettate.append(normalizzata)

    if not accettate:
        return profile, (
            f"Nessuna preferenza salvata: {len(scartate)} voci scartate perché "
            "incomplete. Servono 'item' non vuoto, 'sentiment' fra like/dislike/"
            "neutro e 'peso' intero da 1 a 5."
        ), True

    profile = profile_ops.merge_preferenze(profile, accettate)
    riepilogo = ", ".join(f"{p['item']} ({p['sentiment']}, peso {p['peso']})" for p in accettate)
    testo = f"Salvate {len(accettate)} preferenze: {riepilogo}."
    if scartate:
        # Segnalare lo scarto invece di silenziarlo è la differenza fra un
        # modello che si corregge e uno che non sa di aver fallito.
        testo += f" {len(scartate)} voci scartate perché incomplete."
    return profile, testo, False


def _aggiorna_allergie_intolleranze(profile: dict, argomenti: dict) -> tuple[dict, str, bool]:
    voci = argomenti.get("voci")
    if not isinstance(voci, list):
        return profile, "'voci' deve essere una lista di stringhe.", True

    modo = argomenti.get("modo", "aggiungi")
    if modo not in ("aggiungi", "sostituisci"):
        return profile, "'modo' deve essere 'aggiungi' oppure 'sostituisci'.", True

    prima = list(profile["allergie_intolleranze"])
    profile, finale = profile_ops.aggiorna_allergie(profile, voci, modo)
    if finale == prima:
        return profile, "Elenco allergie invariato: nessuna voce nuova da registrare.", False

    return profile, (
        f"Allergie e intolleranze aggiornate: {', '.join(finale) or 'nessuna'}. "
        "Conferma esplicitamente la modifica all'utente nella tua risposta."
    ), False


def _registra_raccomandazione(profile: dict, argomenti: dict) -> tuple[dict, str, bool]:
    scelta = argomenti.get("scelta_consigliata")
    if not isinstance(scelta, str) or not scelta.strip():
        return profile, "'scelta_consigliata' deve essere una stringa non vuota.", True

    opzioni = argomenti.get("opzioni_presentate")
    if not isinstance(opzioni, list):
        return profile, "'opzioni_presentate' deve essere la lista delle opzioni del menu.", True
    opzioni = [o for o in opzioni if isinstance(o, str) and o.strip()]

    # Backstop deterministico sul vincolo assoluto. Prende solo le occorrenze
    # letterali (mai "besciamella" per il lattosio), quindi non sostituisce il
    # ragionamento chiesto nel system prompt: è una rete di sicurezza in più.
    allergeni = _allergeni_nella_scelta(profile["allergie_intolleranze"], scelta)
    if allergeni:
        return profile, (
            f"Raccomandazione RIFIUTATA: {scelta!r} contiene {', '.join(allergeni)}, "
            "che è un vincolo assoluto. Scegli un'altra opzione del menu e spiega "
            "all'utente perché hai scartato questa."
        ), True

    profile, pasto_id = profile_ops.record_new_meal(profile, opzioni, scelta)
    return profile, f"Pasto registrato con id {pasto_id} (consigliato: {scelta}).", False


def _registra_feedback_pasto(profile: dict, argomenti: dict) -> tuple[dict, str, bool]:
    pasto_id = argomenti.get("pasto_id")
    pasto = next((p for p in profile["pasti_recenti"] if p.get("id") == pasto_id), None)
    if pasto is None:
        disponibili = ", ".join(
            f"{p['id']} ({p.get('scelta_consigliata', '?')})" for p in profile["pasti_recenti"][-5:]
        )
        return profile, (
            f"Nessun pasto con id {pasto_id!r}. Id disponibili: {disponibili or 'nessuno'}."
        ), True

    gradimento = argomenti.get("gradimento")
    if gradimento not in ("positivo", "negativo", "neutro"):
        return profile, "'gradimento' deve essere positivo, negativo o neutro.", True

    testo_feedback = argomenti.get("testo_feedback")
    if not isinstance(testo_feedback, str):
        testo_feedback = ""

    scelta_reale = argomenti.get("scelta_reale")
    if not isinstance(scelta_reale, str) or not scelta_reale.strip():
        scelta_reale = None

    # Le preferenze hanno un tool dedicato: qui si registra solo il pasto.
    profile = profile_ops.apply_feedback(
        profile, pasto_id, gradimento, scelta_reale, testo_feedback, []
    )
    return profile, (
        f"Feedback {gradimento} registrato sul pasto {pasto_id} "
        f"({pasto.get('scelta_consigliata', '?')})."
    ), False


def _segna_onboarding_completato(profile: dict, argomenti: dict) -> tuple[dict, str, bool]:
    if profile["onboarding_completato"]:
        return profile, "L'onboarding risultava già completato.", False

    profile = {**profile, "onboarding_completato": True}
    return profile, "Onboarding completato: d'ora in poi non riproporre le domande iniziali.", False


_DISPATCH = {
    "salva_preferenze": _salva_preferenze,
    "aggiorna_allergie_intolleranze": _aggiorna_allergie_intolleranze,
    "registra_raccomandazione": _registra_raccomandazione,
    "registra_feedback_pasto": _registra_feedback_pasto,
    "segna_onboarding_completato": _segna_onboarding_completato,
}


def esegui_tool(nome: str, argomenti: dict, profile: dict) -> tuple[dict, str, bool]:
    """Applica un tool al profilo.

    Restituisce (profilo_aggiornato, testo_del_tool_result, is_error). Non
    solleva mai: qualunque problema torna al modello come errore, così può
    ritentare con argomenti diversi invece di far saltare tutto il turno.
    """
    funzione = _DISPATCH.get(nome)
    if funzione is None:
        return profile, f"Tool sconosciuto: {nome!r}. Disponibili: {', '.join(sorted(NOMI_TOOL))}.", True

    if not isinstance(argomenti, dict):
        return profile, f"Argomenti non validi per {nome}: atteso un oggetto.", True

    try:
        return funzione(profile, argomenti)
    except Exception as exc:
        logger.exception("Tool %s fallito con argomenti %r", nome, argomenti)
        return profile, f"Il tool {nome} è fallito: {exc}", True
