from unittest.mock import patch

import storage
import handlers


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


def test_handle_feedback_command_senza_pasti_in_attesa():
    profilo = _profilo_base(onboarding_completato=True, pasti_recenti=[])

    _, messaggi = handlers.handle_feedback_command(profilo)

    assert "nessun" in messaggi[0].lower() or "non ho" in messaggi[0].lower()


def test_handle_feedback_command_durante_onboarding_chiede_di_completarlo_prima():
    pasto = {"id": "abc", "data": "2026-07-09", "scelta_consigliata": "insalata", "scelta_reale": None, "gradimento": None, "feedback": None, "opzioni_presentate": ["insalata"]}
    profilo = _profilo_base(onboarding_completato=False, pasti_recenti=[pasto])

    profilo_aggiornato, messaggi = handlers.handle_feedback_command(profilo)

    assert profilo_aggiornato.get("in_attesa_di_feedback_per") is None
    assert "onboarding" in messaggi[0].lower()


def test_handle_feedback_command_con_pasto_in_attesa_chiede_com_e_andata():
    pasto = {"id": "abc", "data": "2026-07-09", "scelta_consigliata": "insalata", "scelta_reale": None, "gradimento": None, "feedback": None, "opzioni_presentate": ["insalata"]}
    profilo = _profilo_base(onboarding_completato=True, pasti_recenti=[pasto])

    profilo_aggiornato, messaggi = handlers.handle_feedback_command(profilo)

    assert profilo_aggiornato["in_attesa_di_feedback_per"] == "abc"
    assert "insalata" in messaggi[0]


@patch("handlers.claude_client.parse_feedback")
def test_handle_feedback_answer_aggiorna_pasto_e_libera_lo_stato(mock_parse_feedback):
    mock_parse_feedback.return_value = {"gradimento": "positivo", "scelta_reale": None, "nuove_preferenze": []}
    pasto = {"id": "abc", "data": "2026-07-09", "scelta_consigliata": "insalata", "scelta_reale": None, "gradimento": None, "feedback": None, "opzioni_presentate": ["insalata"]}
    profilo = _profilo_base(pasti_recenti=[pasto], in_attesa_di_feedback_per="abc")

    profilo_aggiornato, messaggi = handlers.handle_feedback_answer(profilo, "buonissima")

    assert profilo_aggiornato["pasti_recenti"][0]["gradimento"] == "positivo"
    assert profilo_aggiornato["in_attesa_di_feedback_per"] is None
    assert len(messaggi) == 1


def test_handle_feedback_answer_senza_pasto_in_attesa():
    profilo = _profilo_base(pasti_recenti=[], in_attesa_di_feedback_per=None)

    _, messaggi = handlers.handle_feedback_answer(profilo, "buonissima")

    assert "/feedback" in messaggi[0]
