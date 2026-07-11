from unittest.mock import patch

import storage
import handlers


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


@patch("handlers.handle_onboarding_answer")
def test_dispatch_durante_onboarding_richiede_testo(mock_handle):
    profilo = _profilo_base(onboarding_completato=False)

    _, messaggi = handlers.handle_incoming_message(profilo, None, b"foto")

    mock_handle.assert_not_called()
    assert "parole" in messaggi[0].lower() or "foto" in messaggi[0].lower()


@patch("handlers.handle_onboarding_answer")
def test_dispatch_durante_onboarding_instrada_al_testo(mock_handle):
    mock_handle.return_value = ({"foo": "bar"}, ["ok"])
    profilo = _profilo_base(onboarding_completato=False)

    risultato = handlers.handle_incoming_message(profilo, "sono celiaca", None)

    mock_handle.assert_called_once_with(profilo, "sono celiaca")
    assert risultato == ({"foo": "bar"}, ["ok"])


@patch("handlers.handle_reset_confirmation")
def test_dispatch_in_attesa_di_conferma_reset_instrada_alla_conferma(mock_handle):
    mock_handle.return_value = ({"foo": "bar"}, ["ok"])
    profilo = _profilo_base(onboarding_completato=True, in_attesa_di_conferma_reset=True)

    risultato = handlers.handle_incoming_message(profilo, "CONFERMA", None)

    mock_handle.assert_called_once_with(profilo, "CONFERMA")
    assert risultato == ({"foo": "bar"}, ["ok"])


@patch("handlers.handle_feedback_answer")
def test_dispatch_in_attesa_di_feedback_instrada_al_feedback(mock_handle):
    mock_handle.return_value = ({"foo": "bar"}, ["ok"])
    profilo = _profilo_base(onboarding_completato=True, in_attesa_di_feedback_per="abc")

    risultato = handlers.handle_incoming_message(profilo, "buonissimo", None)

    mock_handle.assert_called_once_with(profilo, "buonissimo")
    assert risultato == ({"foo": "bar"}, ["ok"])


@patch("handlers.handle_menu")
def test_dispatch_testo_libero_viene_diviso_in_righe_e_passato_come_menu(mock_handle_menu):
    mock_handle_menu.return_value = ({"foo": "bar"}, ["ok"])
    profilo = _profilo_base(onboarding_completato=True)

    handlers.handle_incoming_message(profilo, "pasta al forno\ninsalata\n", None)

    mock_handle_menu.assert_called_once_with(profilo, ["pasta al forno", "insalata"])


@patch("handlers.handle_menu")
@patch("handlers.claude_client.extract_menu_from_image")
def test_dispatch_foto_estrae_il_menu_e_lo_passa_a_handle_menu(mock_extract, mock_handle_menu):
    mock_extract.return_value = ["pasta al forno", "insalata"]
    mock_handle_menu.return_value = ({"foo": "bar"}, ["ok"])
    profilo = _profilo_base(onboarding_completato=True)

    handlers.handle_incoming_message(profilo, None, b"finti-byte-immagine")

    mock_extract.assert_called_once_with(b"finti-byte-immagine")
    mock_handle_menu.assert_called_once_with(profilo, ["pasta al forno", "insalata"])
