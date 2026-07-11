import storage
import handlers


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


def test_handle_preferenze_command_senza_preferenze():
    profilo = _profilo_base(allergie_intolleranze=[], preferenze=[])

    _, messaggi = handlers.handle_preferenze_command(profilo)

    assert "nessuna" in messaggi[0].lower()


def test_handle_preferenze_command_con_dati():
    profilo = _profilo_base(
        allergie_intolleranze=["glutine"],
        preferenze=[{"item": "broccoli", "sentiment": "dislike", "peso": 4, "fonte": "dichiarato", "note": None}],
    )

    _, messaggi = handlers.handle_preferenze_command(profilo)

    assert "glutine" in messaggi[0]
    assert "broccoli" in messaggi[0]


def test_handle_reset_command_chiede_conferma():
    profilo = _profilo_base()

    profilo_aggiornato, messaggi = handlers.handle_reset_command(profilo)

    assert profilo_aggiornato["in_attesa_di_conferma_reset"] is True
    assert "CONFERMA" in messaggi[0]


def test_handle_reset_confirmation_annulla_se_risposta_diversa_da_conferma():
    profilo = _profilo_base(in_attesa_di_conferma_reset=True, allergie_intolleranze=["glutine"])

    profilo_aggiornato, messaggi = handlers.handle_reset_confirmation(profilo, "no aspetta")

    assert profilo_aggiornato["in_attesa_di_conferma_reset"] is False
    assert profilo_aggiornato["allergie_intolleranze"] == ["glutine"]
    assert "annullato" in messaggi[0].lower()


def test_handle_reset_confirmation_azzera_il_profilo_se_confermato():
    profilo = _profilo_base(in_attesa_di_conferma_reset=True, allergie_intolleranze=["glutine"], chat_id=42)

    profilo_aggiornato, messaggi = handlers.handle_reset_confirmation(profilo, "CONFERMA")

    assert profilo_aggiornato["allergie_intolleranze"] == []
    assert profilo_aggiornato["onboarding_completato"] is False
    assert profilo_aggiornato["chat_id"] == 42
    assert len(messaggi) == 2
