from unittest.mock import MagicMock, patch

import pytest

import claude_client


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
def test_call_json_riprova_su_json_non_valido(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _fake_response("non è json"),
        _fake_response('{"ok": true}'),
    ]
    mock_get_client.return_value = mock_client

    risultato = claude_client._call_json("system", "user")

    assert risultato == {"ok": True}
    assert mock_client.messages.create.call_count == 2


@patch("claude_client._get_client")
def test_call_json_strip_code_fence(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response('```json\n{"ok": true}\n```')
    mock_get_client.return_value = mock_client

    risultato = claude_client._call_json("system", "user")

    assert risultato == {"ok": True}


@patch("claude_client._get_client")
def test_call_json_solleva_errore_dopo_troppi_tentativi(mock_get_client):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_response("mai json")
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError):
        claude_client._call_json("system", "user")


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

    risultato = claude_client._call_json("system", "user")

    assert risultato == {"ok": True}


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
