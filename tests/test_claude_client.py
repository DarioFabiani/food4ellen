from unittest.mock import MagicMock, patch

import pytest

import claude_client
import prompts

_SCHEMA_FINTO = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _fake_response(testo: str):
    fake = MagicMock()
    fake.content = [MagicMock(type="text", text=testo)]
    return fake


@patch("claude_client._get_client")
def test_parse_onboarding_answer_restituisce_dict_parsato(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"allergie_intolleranze": ["glutine"]}')
    mock_get_client.return_value = mock_client

    risultato = claude_client.parse_onboarding_answer(1, "sono celiaca")

    assert risultato == {"allergie_intolleranze": ["glutine"]}


@patch("claude_client._get_client")
def test_call_json_riprova_su_risposta_troncata(mock_get_client):
    """Lo structured output garantisce la forma solo di una risposta completa:
    se il modello viene troncato il JSON resta a metà e serve un altro giro."""
    troncata = _fake_response('{"ok": tr')
    troncata.stop_reason = "max_tokens"

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [troncata, _fake_response('{"ok": true}')]
    mock_get_client.return_value = mock_client

    risultato = claude_client._call_json("system", "user", _SCHEMA_FINTO)

    assert risultato == {"ok": True}
    assert mock_client.messages.create.call_count == 2
    primo, secondo = mock_client.messages.create.call_args_list
    assert secondo.kwargs["max_tokens"] > primo.kwargs["max_tokens"]
    # niente re-prompt: col JSON troncato la correzione è più margine di token
    assert len(secondo.kwargs["messages"]) == 1


@patch("claude_client._get_client")
def test_call_json_passa_lo_schema_come_structured_output(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"ok": true}')
    mock_get_client.return_value = mock_client

    claude_client._call_json("system", "user", _SCHEMA_FINTO)

    output_config = mock_client.messages.create.call_args.kwargs["output_config"]
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["schema"] is _SCHEMA_FINTO


@patch("claude_client._get_client")
def test_call_json_solleva_errore_dopo_troppi_tentativi(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response("mai json")
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError):
        claude_client._call_json("system", "user", _SCHEMA_FINTO)


@patch("claude_client._get_client")
def test_call_json_ignora_blocco_di_thinking_prima_del_testo(mock_get_client):
    """Regressione: il modello può anteporre un ThinkingBlock al testo;
    prendere ciecamente content[0] causava un AttributeError in produzione."""
    mock_client = MagicMock()
    fake = MagicMock()
    fake.content = [
        MagicMock(type="thinking", text=None),
        MagicMock(type="text", text='{"ok": true}'),
    ]
    mock_client.messages.create.return_value = fake
    mock_get_client.return_value = mock_client

    risultato = claude_client._call_json("system", "user", _SCHEMA_FINTO)

    assert risultato == {"ok": True}


@patch("claude_client._get_client")
def test_call_json_riprova_se_la_risposta_non_ha_blocchi_di_testo(mock_get_client):
    """Regressione BUG-1: se il budget di token finisce nel thinking la risposta
    non ha blocchi text; l'estrazione deve stare dentro il try e il retry deve
    alzare max_tokens invece di far esplodere l'handler."""
    senza_testo = MagicMock()
    senza_testo.content = [MagicMock(type="thinking", text=None)]
    senza_testo.stop_reason = "max_tokens"

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [senza_testo, _fake_response('{"ok": true}')]
    mock_get_client.return_value = mock_client

    risultato = claude_client._call_json("system", "user", _SCHEMA_FINTO)

    assert risultato == {"ok": True}
    assert mock_client.messages.create.call_count == 2
    primo, secondo = mock_client.messages.create.call_args_list
    assert secondo.kwargs["max_tokens"] > primo.kwargs["max_tokens"]
    # niente botta e risposta col modello: non c'era testo da rimandare
    assert len(secondo.kwargs["messages"]) == 1


@patch("claude_client._get_client")
def test_call_json_solleva_se_nessuna_risposta_ha_testo(mock_get_client):
    senza_testo = MagicMock()
    senza_testo.content = [MagicMock(type="thinking", text=None)]
    senza_testo.stop_reason = "max_tokens"

    mock_client = MagicMock()
    mock_client.messages.create.return_value = senza_testo
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError):
        claude_client._call_json("system", "user", _SCHEMA_FINTO)

    assert mock_client.messages.create.call_count == claude_client.MAX_JSON_RETRIES + 1


@patch("claude_client._get_client")
def test_call_json_passa_effort_e_max_tokens(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"ok": true}')
    mock_get_client.return_value = mock_client

    claude_client._call_json("system", "user", _SCHEMA_FINTO)

    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["output_config"]["effort"] == "low"
    assert kwargs["max_tokens"] == 2048


@patch("claude_client._get_client")
def test_get_recommendation_usa_effort_medium(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        '{"scelta_consigliata": "insalata", "messaggio": "ok", "alternativa": null}'
    )
    mock_get_client.return_value = mock_client

    claude_client.get_recommendation(["insalata"], [], [], [], "")

    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["output_config"]["effort"] == "medium"
    assert kwargs["max_tokens"] == 2048


@patch("claude_client._get_client")
def test_extract_menu_from_image_usa_effort_medium(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"opzioni_menu": []}')
    mock_get_client.return_value = mock_client

    claude_client.extract_menu_from_image(b"finti-byte")

    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["output_config"]["effort"] == "medium"
    assert kwargs["max_tokens"] == 4096


@patch("claude_client._get_client")
def test_update_riassunto_storico_usa_effort_low_e_max_tokens_alzato(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response("riassunto")
    mock_get_client.return_value = mock_client

    pasto = {"data": "2026-07-09", "scelta_consigliata": "insalata", "scelta_reale": None, "gradimento": None, "feedback": None}
    claude_client.update_riassunto_storico("", pasto)

    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["output_config"]["effort"] == "low"
    assert kwargs["max_tokens"] == 1024


@patch("claude_client._get_client")
def test_parse_onboarding_answer_usa_effort_low(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"allergie_intolleranze": []}')
    mock_get_client.return_value = mock_client

    claude_client.parse_onboarding_answer(1, "nessuna")

    assert mock_client.messages.create.call_args.kwargs["output_config"]["effort"] == "low"


@patch("claude_client._get_client")
def test_parse_feedback_usa_effort_low(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        '{"gradimento": "positivo", "scelta_reale": null, "nuove_preferenze": []}'
    )
    mock_get_client.return_value = mock_client

    pasto = {"id": "1", "data": "2026-07-09", "scelta_consigliata": "insalata", "opzioni_presentate": ["insalata"]}
    claude_client.parse_feedback(pasto, [], "buona")

    assert mock_client.messages.create.call_args.kwargs["output_config"]["effort"] == "low"


@patch("claude_client._get_client")
def test_extract_menu_from_image_restituisce_lista_opzioni(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"opzioni_menu": ["pasta", "insalata"]}')
    mock_get_client.return_value = mock_client

    risultato = claude_client.extract_menu_from_image(b"finti-byte-immagine")

    assert risultato == ["pasta", "insalata"]
    kwargs = mock_client.messages.create.call_args.kwargs
    content_blocks = kwargs["messages"][0]["content"]
    assert content_blocks[0]["type"] == "image"
    # Un menu fotografato può avere molte voci: serve margine di token
    # sufficiente a non troncare il JSON (vedi bug #immagine).
    assert kwargs["max_tokens"] > 1024


@patch("claude_client._get_client")
def test_get_recommendation_restituisce_dict_con_scelta_e_messaggio(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        '{"scelta_consigliata": "insalata", "messaggio": "Prendi l\'insalata!", "alternativa": null}'
    )
    mock_get_client.return_value = mock_client

    risultato = claude_client.get_recommendation(["pasta", "insalata"], [], [], [], "")

    assert risultato["scelta_consigliata"] == "insalata"


@patch("claude_client._get_client")
def test_parse_feedback_restituisce_dict(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        '{"gradimento": "positivo", "scelta_reale": null, "nuove_preferenze": []}'
    )
    mock_get_client.return_value = mock_client

    pasto = {"id": "1", "data": "2026-07-09", "scelta_consigliata": "insalata", "opzioni_presentate": ["insalata"]}
    risultato = claude_client.parse_feedback(pasto, [], "buonissima")

    assert risultato["gradimento"] == "positivo"


@patch("claude_client._get_client")
def test_update_riassunto_storico_restituisce_testo(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response("Preferisce pasti leggeri a pranzo.")
    mock_get_client.return_value = mock_client

    pasto = {"data": "2026-07-09", "scelta_consigliata": "insalata", "scelta_reale": None, "gradimento": "positivo", "feedback": None}
    risultato = claude_client.update_riassunto_storico("", pasto)

    assert risultato == "Preferisce pasti leggeri a pranzo."


# ---------------------------------------------------------------------------
# structured outputs: schema giusto per ciascun call-site
# ---------------------------------------------------------------------------


def _schema_usato(mock_client) -> dict:
    return mock_client.messages.create.call_args.kwargs["output_config"]["format"]["schema"]


@patch("claude_client._get_client")
def test_parse_onboarding_answer_step_1_usa_lo_schema_allergie(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"allergie_intolleranze": []}')
    mock_get_client.return_value = mock_client

    claude_client.parse_onboarding_answer(1, "nessuna")

    assert _schema_usato(mock_client) is prompts.SCHEMA_ONBOARDING_ALLERGIE


@patch("claude_client._get_client")
def test_parse_onboarding_answer_step_successivo_usa_lo_schema_preferenze(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"preferenze": []}')
    mock_get_client.return_value = mock_client

    claude_client.parse_onboarding_answer(2, "odio i broccoli")

    assert _schema_usato(mock_client) is prompts.SCHEMA_ONBOARDING_PREFERENZE


@patch("claude_client._get_client")
def test_extract_menu_from_image_usa_lo_schema_menu(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('{"opzioni_menu": []}')
    mock_get_client.return_value = mock_client

    claude_client.extract_menu_from_image(b"finti-byte")

    assert _schema_usato(mock_client) is prompts.SCHEMA_MENU_VISION


@patch("claude_client._get_client")
def test_get_recommendation_usa_lo_schema_recommendation(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        '{"scelta_consigliata": "insalata", "messaggio": "ok", "alternativa": null}'
    )
    mock_get_client.return_value = mock_client

    claude_client.get_recommendation(["insalata"], [], [], [], "")

    assert _schema_usato(mock_client) is prompts.SCHEMA_RECOMMENDATION


@patch("claude_client._get_client")
def test_parse_feedback_usa_lo_schema_feedback(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response(
        '{"gradimento": "positivo", "scelta_reale": null, "nuove_preferenze": []}'
    )
    mock_get_client.return_value = mock_client

    pasto = {"id": "1", "data": "2026-07-09", "scelta_consigliata": "insalata", "opzioni_presentate": ["insalata"]}
    claude_client.parse_feedback(pasto, [], "buona")

    assert _schema_usato(mock_client) is prompts.SCHEMA_FEEDBACK


@patch("claude_client._get_client")
def test_update_riassunto_storico_non_usa_structured_output(mock_get_client):
    """Il riassunto è testo libero: niente format, altrimenti l'API rifiuta."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response("riassunto")
    mock_get_client.return_value = mock_client

    pasto = {"data": "2026-07-09", "scelta_consigliata": "insalata", "scelta_reale": None, "gradimento": None, "feedback": None}
    claude_client.update_riassunto_storico("", pasto)

    assert "format" not in mock_client.messages.create.call_args.kwargs["output_config"]
