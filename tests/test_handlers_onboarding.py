from unittest.mock import patch

import storage
import handlers


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


def test_handle_start_onboarding_non_completato_fa_la_prima_domanda():
    profilo = _profilo_base(onboarding_completato=False, onboarding_step=1)

    profilo_aggiornato, messaggi = handlers.handle_start(profilo)

    assert any("allergie" in m.lower() for m in messaggi)


def test_handle_start_onboarding_completato_da_il_benvenuto():
    profilo = _profilo_base(onboarding_completato=True)

    _, messaggi = handlers.handle_start(profilo)

    assert len(messaggi) == 1
    assert "menu" in messaggi[0].lower()


@patch("handlers.claude_client.parse_onboarding_answer")
def test_handle_onboarding_answer_step_1_salva_allergie_e_passa_a_step_2(mock_parse):
    mock_parse.return_value = {"allergie_intolleranze": ["glutine"]}
    profilo = _profilo_base(onboarding_step=1)

    profilo_aggiornato, messaggi = handlers.handle_onboarding_answer(profilo, "sono celiaca")

    assert profilo_aggiornato["allergie_intolleranze"] == ["glutine"]
    assert profilo_aggiornato["onboarding_step"] == 2
    assert profilo_aggiornato["onboarding_completato"] is False
    assert len(messaggi) == 1


@patch("handlers.claude_client.parse_onboarding_answer")
def test_handle_onboarding_answer_step_intermedio_salva_preferenze(mock_parse):
    mock_parse.return_value = {
        "preferenze": [{"item": "broccoli", "sentiment": "dislike", "peso": 4, "fonte": "dichiarato", "note": None}]
    }
    profilo = _profilo_base(onboarding_step=2)

    profilo_aggiornato, _ = handlers.handle_onboarding_answer(profilo, "odio i broccoli")

    assert profilo_aggiornato["preferenze"][0]["item"] == "broccoli"
    assert profilo_aggiornato["onboarding_step"] == 3


@patch("handlers.claude_client.parse_onboarding_answer")
def test_handle_onboarding_answer_ultimo_step_completa_onboarding(mock_parse):
    mock_parse.return_value = {"preferenze": []}
    profilo = _profilo_base(onboarding_step=4)

    profilo_aggiornato, messaggi = handlers.handle_onboarding_answer(profilo, "niente in particolare")

    assert profilo_aggiornato["onboarding_completato"] is True
    assert any("preferenze" in m.lower() or "consiglio" in m.lower() or "menu" in m.lower() for m in messaggi)
