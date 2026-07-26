"""Test del loop agentico e delle due chiamate one-shot rimaste."""
import copy
import json
from unittest.mock import MagicMock, patch

import pytest

import claude_client
import storage
from conftest import (
    blocco_testo,
    blocco_thinking,
    blocco_tool_use,
    risposta,
    risposta_testo,
    risposta_tool_use,
)

SYSTEM = "sei un assistente"


def _profilo_base(**overrides) -> dict:
    profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    profilo.update(overrides)
    return profilo


def _client_con(*risposte):
    client = MagicMock()
    client.messages.create.side_effect = list(risposte)
    return client


PREFERENZA = {
    "item": "zucchine",
    "sentiment": "like",
    "peso": 4,
    "fonte": "inferito",
    "note": None,
}


# --- loop agentico: percorso base ----------------------------------------


def test_agente_senza_tool_restituisce_il_testo():
    client = _client_con(risposta_testo("Oggi ti consiglio l'insalata."))

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "Oggi ti consiglio l'insalata."
    assert client.messages.create.call_count == 1


def test_agente_applica_il_tool_e_poi_risponde():
    client = _client_con(
        risposta_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}),
        risposta_testo("Segnato!"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "adoro le zucchine", _profilo_base())

    assert testo == "Segnato!"
    assert profilo["preferenze"] == [PREFERENZA]


def test_agente_incatena_piu_giri_di_tool():
    client = _client_con(
        risposta_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}, "tu_1"),
        risposta_tool_use("segna_onboarding_completato", {}, "tu_2"),
        risposta_testo("Pronti!"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, _ = claude_client.esegui_agente(SYSTEM, "finito", _profilo_base())

    assert profilo["preferenze"] == [PREFERENZA]
    assert profilo["onboarding_completato"] is True
    assert client.messages.create.call_count == 3


def test_agente_rimanda_i_tool_result_come_turno_utente():
    client = _client_con(
        risposta_tool_use("segna_onboarding_completato", {}, "tu_42"),
        risposta_testo("ok"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    messaggi = client.messages.create.call_args_list[1].kwargs["messages"]
    assert messaggi[1]["role"] == "assistant"
    assert messaggi[2]["role"] == "user"
    risultato = messaggi[2]["content"][0]
    assert risultato["type"] == "tool_result"
    assert risultato["tool_use_id"] == "tu_42"
    assert risultato["is_error"] is False


def test_agente_mette_i_tool_paralleli_in_un_solo_messaggio_utente():
    """Spezzarli insegnerebbe al modello a non chiamare più i tool in parallelo."""
    client = _client_con(
        risposta(
            blocco_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}, "tu_a"),
            blocco_tool_use("segna_onboarding_completato", {}, "tu_b"),
            stop_reason="tool_use",
        ),
        risposta_testo("fatto"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, _ = claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    messaggi = client.messages.create.call_args_list[1].kwargs["messages"]
    risultati = messaggi[2]["content"]
    assert [r["tool_use_id"] for r in risultati] == ["tu_a", "tu_b"]
    assert profilo["preferenze"] == [PREFERENZA]
    assert profilo["onboarding_completato"] is True


def test_agente_rimanda_indietro_i_blocchi_di_thinking_inalterati():
    """Con il thinking attivo i blocchi di ragionamento devono tornare intatti
    insieme ai tool_use, altrimenti l'API rifiuta il turno successivo."""
    pensiero = blocco_thinking()
    uso = blocco_tool_use("segna_onboarding_completato", {})
    client = _client_con(risposta(pensiero, uso, stop_reason="tool_use"), risposta_testo("ok"))

    with patch.object(claude_client, "_get_client", return_value=client):
        claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    contenuto_assistant = client.messages.create.call_args_list[1].kwargs["messages"][1]["content"]
    assert contenuto_assistant == [pensiero, uso]


def test_agente_scarta_i_blocchi_di_thinking_dal_testo_finale():
    client = _client_con(risposta(blocco_thinking(), blocco_testo("Prendi l'insalata.")))

    with patch.object(claude_client, "_get_client", return_value=client):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "Prendi l'insalata."


def test_agente_concatena_piu_blocchi_di_testo():
    client = _client_con(risposta(blocco_testo("Primo."), blocco_testo("Secondo.")))

    with patch.object(claude_client, "_get_client", return_value=client):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "Primo.\n\nSecondo."


# --- loop agentico: casi limite ------------------------------------------


def test_agente_ritenta_con_piu_margine_se_la_risposta_e_troncata():
    client = _client_con(
        risposta(blocco_thinking(), stop_reason="max_tokens"),
        risposta_testo("eccomi"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base(), max_tokens=1000)

    assert testo == "eccomi"
    chiamate = client.messages.create.call_args_list
    assert chiamate[0].kwargs["max_tokens"] == 1000
    assert chiamate[1].kwargs["max_tokens"] == 2000


def test_agente_non_ritenta_il_troncamento_all_infinito():
    """Un secondo max_tokens di fila non fa ripartire il raddoppio: si chiude
    col testo che c'è, altrimenti si bruciano tutte le iterazioni."""
    client = _client_con(
        risposta(blocco_thinking(), stop_reason="max_tokens"),
        risposta(blocco_testo("parziale"), stop_reason="max_tokens"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "parziale"
    assert client.messages.create.call_count == 2


def test_agente_esaurite_le_iterazioni_chiude_senza_tool():
    tool_infiniti = [
        risposta_tool_use("segna_onboarding_completato", {})
        for _ in range(claude_client.MAX_ITERAZIONI_AGENTE)
    ]
    client = _client_con(*tool_infiniti, risposta_testo("chiudo qui"))

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "vai", _profilo_base())

    assert testo == "chiudo qui"
    # L'ultima chiamata vieta i tool: l'utente deve ricevere comunque parole.
    ultima = client.messages.create.call_args_list[-1].kwargs
    assert ultima["tool_choice"] == {"type": "none"}
    assert profilo["onboarding_completato"] is True


def test_agente_senza_testo_finale_usa_il_ripiego_e_conserva_il_profilo():
    client = _client_con(
        risposta_tool_use("salva_preferenze", {"preferenze": [PREFERENZA]}),
        risposta(blocco_thinking()),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == claude_client.RISPOSTA_DI_RIPIEGO
    # I tool erano già stati eseguiti: il profilo non va perso.
    assert profilo["preferenze"] == [PREFERENZA]


def test_agente_propaga_l_errore_di_un_tool_senza_fermarsi():
    client = _client_con(
        risposta_tool_use("registra_feedback_pasto", {"pasto_id": "boh", "gradimento": "positivo",
                                                     "scelta_reale": None, "testo_feedback": "x"}),
        risposta_testo("scusa, ho sbagliato"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        _, testo = claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert testo == "scusa, ho sbagliato"
    risultato = client.messages.create.call_args_list[1].kwargs["messages"][2]["content"][0]
    assert risultato["is_error"] is True


def test_agente_ignora_un_tool_use_con_nome_sconosciuto():
    client = _client_con(
        risposta_tool_use("cancella_tutto", {}),
        risposta_testo("non posso"),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, testo = claude_client.esegui_agente(SYSTEM, "cancella", _profilo_base())

    assert testo == "non posso"
    assert profilo == _profilo_base()


# --- parametri della chiamata --------------------------------------------


def test_agente_passa_tool_thinking_e_system_cacheabile():
    client = _client_con(risposta_testo("ok"))

    with patch.object(claude_client, "_get_client", return_value=client):
        claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tools"] is not None and len(kwargs["tools"]) == 5
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "medium"}
    # Il breakpoint di cache copre tools + system, che si ripetono ad ogni giro.
    assert kwargs["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]


def test_agente_non_passa_tool_choice_nei_giri_normali():
    client = _client_con(risposta_testo("ok"))

    with patch.object(claude_client, "_get_client", return_value=client):
        claude_client.esegui_agente(SYSTEM, "ciao", _profilo_base())

    assert "tool_choice" not in client.messages.create.call_args.kwargs


# --- descrivi_immagine ----------------------------------------------------


def _risposta_json(payload: dict):
    return risposta_testo(json.dumps(payload))


def test_descrivi_immagine_rende_un_menu_come_elenco_quotato():
    client = _client_con(
        _risposta_json({"tipo": "menu", "opzioni_menu": ["lasagne (con besciamella)"], "descrizione": "menu"})
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        testo = claude_client.descrivi_immagine(b"finti-byte", "image/png")

    assert testo == "tipo: menu\n- 'lasagne (con besciamella)'"


def test_descrivi_immagine_rende_una_foto_qualsiasi_come_descrizione():
    client = _client_con(
        _risposta_json({"tipo": "altro", "opzioni_menu": [], "descrizione": "un piatto di pasta"})
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        testo = claude_client.descrivi_immagine(b"finti-byte")

    assert "un piatto di pasta" in testo
    assert testo.startswith("tipo: altro")


def test_descrivi_immagine_ripiega_su_altro_se_il_menu_e_vuoto():
    client = _client_con(
        _risposta_json({"tipo": "menu", "opzioni_menu": [], "descrizione": "foto sfocata"})
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        testo = claude_client.descrivi_immagine(b"finti-byte")

    assert testo.startswith("tipo: altro")


def test_descrivi_immagine_passa_il_media_type_e_lo_schema():
    client = _client_con(
        _risposta_json({"tipo": "altro", "opzioni_menu": [], "descrizione": "x"})
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        claude_client.descrivi_immagine(b"finti-byte", "image/webp")

    kwargs = client.messages.create.call_args.kwargs
    immagine = kwargs["messages"][0]["content"][0]
    assert immagine["source"]["media_type"] == "image/webp"
    assert kwargs["output_config"]["format"]["schema"]["properties"]["tipo"]["enum"] == ["menu", "altro"]


# --- _call_json: retry sul troncamento ------------------------------------


def test_call_json_raddoppia_i_token_se_manca_il_blocco_di_testo():
    client = _client_con(
        risposta(blocco_thinking(), stop_reason="max_tokens"),
        _risposta_json({"tipo": "altro", "opzioni_menu": [], "descrizione": "ok"}),
    )

    with patch.object(claude_client, "_get_client", return_value=client):
        risultato = claude_client._call_json("s", "u", {"type": "object"}, max_tokens=100)

    assert risultato["descrizione"] == "ok"
    chiamate = client.messages.create.call_args_list
    assert chiamate[0].kwargs["max_tokens"] == 100
    assert chiamate[1].kwargs["max_tokens"] == 200


def test_call_json_solleva_dopo_i_tentativi_esauriti():
    client = _client_con(*[risposta(blocco_thinking()) for _ in range(claude_client.MAX_JSON_RETRIES + 1)])

    with patch.object(claude_client, "_get_client", return_value=client):
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
    client = _client_con(risposta_testo("  Preferisce pasti leggeri.  "))

    with patch.object(claude_client, "_get_client", return_value=client):
        risultato = claude_client.update_riassunto_storico("vecchio", PASTO)

    assert risultato == "Preferisce pasti leggeri."


def test_update_riassunto_storico_non_usa_i_tool():
    client = _client_con(risposta_testo("riassunto"))

    with patch.object(claude_client, "_get_client", return_value=client):
        claude_client.update_riassunto_storico("", PASTO)

    assert "tools" not in client.messages.create.call_args.kwargs


def test_update_riassunto_storico_solleva_se_non_ce_testo():
    client = _client_con(risposta(blocco_thinking()))

    with patch.object(claude_client, "_get_client", return_value=client):
        with pytest.raises(ValueError):
            claude_client.update_riassunto_storico("", PASTO)
