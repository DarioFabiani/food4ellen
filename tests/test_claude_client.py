"""Test del loop agentico e delle due chiamate one-shot rimaste."""
import copy
import json
from unittest.mock import MagicMock, patch

import pytest

import claude_client
import storage
from conftest import (
    messaggio,
    risposta,
    risposta_testo,
    risposta_tool_use,
    risposta_troncata,
    risposta_vuota,
    tool_call,
)

SYSTEM = "sei un assistente"


def _profilo_base(**overrides) -> dict:
    profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    profilo.update(overrides)
    return profilo


def _completion_con(*risposte):
    mock = MagicMock()
    mock.side_effect = list(risposte)
    return mock


PREFERENZA = {
    "item": "zucchine",
    "sentiment": "like",
    "peso": 4,
    "fonte": "inferito",
    "note": None,
}


# --- loop agentico: percorso base ----------------------------------------


def test_agente_senza_tool_restituisce_il_testo():
    completion = _completion_con(risposta_testo("Oggi ti consiglio l'insalata."))

    with patch.object(claude_client, "_completion", completion):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "Oggi ti consiglio l'insalata."
    assert completion.call_count == 1


def test_agente_applica_il_tool_e_poi_risponde():
    completion = _completion_con(
        risposta_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}),
        risposta_testo("Segnato!"),
    )

    with patch.object(claude_client, "_completion", completion):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "adoro le zucchine", _profilo_base())

    assert testo == "Segnato!"
    assert profilo["preferenze"] == [PREFERENZA]


def test_agente_incatena_piu_giri_di_tool():
    completion = _completion_con(
        risposta_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}, "tu_1"),
        risposta_tool_use("segna_onboarding_completato", {}, "tu_2"),
        risposta_testo("Pronti!"),
    )

    with patch.object(claude_client, "_completion", completion):
        profilo, _ = claude_client.esegui_agente(SYSTEM, "finito", _profilo_base())

    assert profilo["preferenze"] == [PREFERENZA]
    assert profilo["onboarding_completato"] is True
    assert completion.call_count == 3


def test_agente_rimanda_i_tool_result_come_messaggi_tool():
    completion = _completion_con(
        risposta_tool_use("segna_onboarding_completato", {}, "tu_42"),
        risposta_testo("ok"),
    )

    with patch.object(claude_client, "_completion", completion):
        claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    messaggi = completion.call_args_list[1].kwargs["messages"]
    # [0] system, [1] user iniziale, [2] assistant col tool_call, [3] risultato.
    assert messaggi[2]["role"] == "assistant"
    assert messaggi[2]["tool_calls"][0]["id"] == "tu_42"
    assert messaggi[3] == {"role": "tool", "tool_call_id": "tu_42", "content": "Onboarding completato: d'ora in poi non riproporre le domande iniziali."}


def test_agente_mette_i_tool_paralleli_in_messaggi_tool_distinti():
    # La risposta con due tool paralleli va costruita a mano: conftest non
    # offre una factory con più tool_calls in un colpo.

    completion = _completion_con(
        risposta(
            messaggio(tool_calls=[
                tool_call("salva_preferenze", {"preferenze": [PREFERENZA]}, "tu_a"),
                tool_call("segna_onboarding_completato", {}, "tu_b"),
            ]),
            finish_reason="tool_calls",
        ),
        risposta_testo("fatto"),
    )

    with patch.object(claude_client, "_completion", completion):
        profilo, _ = claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    messaggi = completion.call_args_list[1].kwargs["messages"]
    risultati_tool = [m for m in messaggi if m["role"] == "tool"]
    assert [r["tool_call_id"] for r in risultati_tool] == ["tu_a", "tu_b"]
    assert profilo["preferenze"] == [PREFERENZA]
    assert profilo["onboarding_completato"] is True


def test_agente_rimanda_indietro_il_reasoning_content_inalterato():
    """Con modelli che ragionano il reasoning va rimandato insieme ai
    tool_calls, altrimenti si perde continuità nel turno successivo."""

    completion = _completion_con(
        risposta(
            messaggio(tool_calls=[tool_call("segna_onboarding_completato", {})], reasoning_content="sto ragionando"),
            finish_reason="tool_calls",
        ),
        risposta_testo("ok"),
    )

    with patch.object(claude_client, "_completion", completion):
        claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    messaggio_assistant = completion.call_args_list[1].kwargs["messages"][2]
    assert messaggio_assistant["reasoning_content"] == "sto ragionando"
    assert messaggio_assistant["tool_calls"][0]["function"]["name"] == "segna_onboarding_completato"


# --- loop agentico: casi limite ------------------------------------------


def test_agente_ritenta_con_piu_margine_se_la_risposta_e_troncata():
    completion = _completion_con(
        risposta_troncata(),
        risposta_testo("eccomi"),
    )

    with patch.object(claude_client, "_completion", completion):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base(), max_tokens=1000)

    assert testo == "eccomi"
    chiamate = completion.call_args_list
    assert chiamate[0].kwargs["max_tokens"] == 1000
    assert chiamate[1].kwargs["max_tokens"] == 2000


def test_agente_non_ritenta_il_troncamento_all_infinito():
    """Un secondo finish_reason="length" di fila non fa ripartire il
    raddoppio: si chiude col testo che c'è, altrimenti si bruciano tutte le
    iterazioni."""

    completion = _completion_con(
        risposta_troncata(),
        risposta(messaggio("parziale"), finish_reason="length"),
    )

    with patch.object(claude_client, "_completion", completion):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "parziale"
    assert completion.call_count == 2


def test_agente_esaurite_le_iterazioni_chiude_senza_tool():
    tool_infiniti = [
        risposta_tool_use("segna_onboarding_completato", {})
        for _ in range(claude_client.MAX_ITERAZIONI_AGENTE)
    ]
    completion = _completion_con(*tool_infiniti, risposta_testo("chiudo qui"))

    with patch.object(claude_client, "_completion", completion):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    assert testo == "chiudo qui"
    # L'ultima chiamata vieta i tool: l'utente deve ricevere comunque parole.
    ultima = completion.call_args_list[-1].kwargs
    assert ultima["tool_choice"] == "none"
    assert profilo["onboarding_completato"] is True


def test_agente_senza_testo_finale_usa_il_ripiego_e_conserva_il_profilo():
    completion = _completion_con(
        risposta_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}),
        risposta_vuota(),
    )

    with patch.object(claude_client, "_completion", completion):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == claude_client.RISPOSTA_DI_RIPIEGO
    # I tool erano già stati eseguiti: il profilo non va perso.
    assert profilo["preferenze"] == [PREFERENZA]


def test_agente_propaga_l_errore_di_un_tool_senza_fermarsi():
    completion = _completion_con(
        risposta_tool_use("registra_feedback_pasto", {"pasto_id": "boh", "gradimento": "positivo",
                                                     "scelta_reale": None, "testo_feedback": "x"}),
        risposta_testo("scusa, ho sbagliato"),
    )

    with patch.object(claude_client, "_completion", completion):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "scusa, ho sbagliato"
    messaggi = completion.call_args_list[1].kwargs["messages"]
    risultato = next(m for m in messaggi if m["role"] == "tool")
    assert risultato["content"].startswith("ERRORE: ")


def test_agente_ignora_un_tool_use_con_nome_sconosciuto():
    completion = _completion_con(
        risposta_tool_use("cancella_tutto", {}),
        risposta_testo("non posso"),
    )

    with patch.object(claude_client, "_completion", completion):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "cancella", _profilo_base())

    assert testo == "non posso"
    assert profilo == _profilo_base()


# --- parametri della chiamata --------------------------------------------


def test_agente_passa_tool_reasoning_effort_e_system_cacheabile():
    completion = _completion_con(risposta_testo("ok"))

    with patch.object(claude_client, "_completion", completion):
        claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    kwargs = completion.call_args.kwargs
    assert kwargs["tools"] is not None and len(kwargs["tools"]) == 5
    assert kwargs["reasoning_effort"] == "medium"
    # Il breakpoint di cache copre tools + system, che si ripetono ad ogni giro
    # (solo per Anthropic: MODEL di default è anthropic/claude-sonnet-5).
    assert kwargs["messages"][0] == {
        "role": "system",
        "content": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
    }
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["tools"][0]


def test_agente_non_passa_tool_choice_nei_giri_normali():
    completion = _completion_con(risposta_testo("ok"))

    with patch.object(claude_client, "_completion", completion):
        claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert "tool_choice" not in completion.call_args.kwargs


# --- descrivi_immagine ----------------------------------------------------


def _risposta_json(payload: dict):
    return risposta_testo(json.dumps(payload))


def test_descrivi_immagine_rende_un_menu_come_elenco_quotato():
    completion = _completion_con(
        _risposta_json({"tipo": "menu", "opzioni_menu": ["lasagne (con besciamella)"], "descrizione": "menu"})
    )

    with patch.object(claude_client, "_completion", completion):
        testo = claude_client.descrivi_immagine(b"finti-byte", "image/png")

    assert testo == "tipo: menu\n- 'lasagne (con besciamella)'"


def test_descrivi_immagine_rende_una_foto_qualsiasi_come_descrizione():
    completion = _completion_con(
        _risposta_json({"tipo": "altro", "opzioni_menu": [], "descrizione": "un piatto di pasta"})
    )

    with patch.object(claude_client, "_completion", completion):
        testo = claude_client.descrivi_immagine(b"finti-byte")

    assert "un piatto di pasta" in testo
    assert testo.startswith("tipo: altro")


def test_descrivi_immagine_ripiega_su_altro_se_il_menu_e_vuoto():
    completion = _completion_con(
        _risposta_json({"tipo": "menu", "opzioni_menu": [], "descrizione": "foto sfocata"})
    )

    with patch.object(claude_client, "_completion", completion):
        testo = claude_client.descrivi_immagine(b"finti-byte")

    assert testo.startswith("tipo: altro")


def test_descrivi_immagine_passa_il_media_type_e_lo_schema():
    completion = _completion_con(
        _risposta_json({"tipo": "altro", "opzioni_menu": [], "descrizione": "x"})
    )

    with patch.object(claude_client, "_completion", completion):
        claude_client.descrivi_immagine(b"finti-byte", "image/webp")

    kwargs = completion.call_args.kwargs
    immagine = kwargs["messages"][1]["content"][0]
    assert immagine["image_url"]["url"].startswith("data:image/webp;base64,")
    assert kwargs["response_format"]["json_schema"]["schema"]["properties"]["tipo"]["enum"] == ["menu", "altro"]


# --- _call_json: retry sul troncamento ------------------------------------


def test_call_json_raddoppia_i_token_se_manca_il_blocco_di_testo():
    completion = _completion_con(
        risposta_troncata(),
        _risposta_json({"tipo": "altro", "opzioni_menu": [], "descrizione": "ok"}),
    )

    with patch.object(claude_client, "_completion", completion):
        risultato = claude_client._call_json("s", "u", {"type": "object"}, max_tokens=100)

    assert risultato["descrizione"] == "ok"
    chiamate = completion.call_args_list
    assert chiamate[0].kwargs["max_tokens"] == 100
    assert chiamate[1].kwargs["max_tokens"] == 200


def test_call_json_solleva_dopo_i_tentativi_esauriti():
    completion = _completion_con(*[risposta_vuota() for _ in range(claude_client.MAX_JSON_RETRIES + 1)])

    with patch.object(claude_client, "_completion", completion):
        with pytest.raises(ValueError, match="JSON valido"):
            claude_client._call_json("s", "u", {"type": "object"})


# --- update_riassunto_storico ---------------------------------------------


PASTO = {
    "data": "2026-07-24",
    "scelta_consigliata": "insalata",
    "scelta_reale": None,
    "gradimento": "positivo",
    "feedback": "buona",
}


def test_update_riassunto_storico_restituisce_il_testo_ripulito():
    completion = _completion_con(risposta_testo("  Preferisce pasti leggeri.  "))

    with patch.object(claude_client, "_completion", completion):
        risultato = claude_client.update_riassunto_storico("vecchio", PASTO)

    assert risultato == "Preferisce pasti leggeri."


def test_update_riassunto_storico_non_usa_i_tool():
    completion = _completion_con(risposta_testo("riassunto"))

    with patch.object(claude_client, "_completion", completion):
        claude_client.update_riassunto_storico("", PASTO)

    assert "tools" not in completion.call_args.kwargs


def test_update_riassunto_storico_solleva_se_non_ce_testo():
    completion = _completion_con(risposta_vuota())

    with patch.object(claude_client, "_completion", completion):
        with pytest.raises(ValueError):
            claude_client.update_riassunto_storico("", PASTO)
