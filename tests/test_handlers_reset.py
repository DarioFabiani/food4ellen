"""Il reset è l'unica azione distruttiva: resta deterministico e con conferma."""
import copy

import handlers
import storage


def _profilo_base(**overrides) -> dict:
    profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    profilo.update(overrides)
    return profilo


def test_reset_chiede_conferma_senza_toccare_nulla():
    profilo = _profilo_base(onboarding_completato=True, allergie_intolleranze=["glutine"])

    risultato, messaggi = handlers.handle_reset_command(profilo)

    assert risultato["in_attesa_di_conferma_reset"] is True
    assert risultato["allergie_intolleranze"] == ["glutine"]
    assert "CONFERMA" in messaggi[0]


def test_reset_e_ammesso_anche_durante_l_onboarding():
    """Con l'onboarding condotto dall'agente non c'è più uno stato da proteggere."""
    risultato, _ = handlers.handle_reset_command(_profilo_base(onboarding_completato=False))

    assert risultato["in_attesa_di_conferma_reset"] is True


def test_conferma_azzera_il_profilo_ma_conserva_il_chat_id():
    profilo = _profilo_base(
        in_attesa_di_conferma_reset=True,
        onboarding_completato=True,
        allergie_intolleranze=["glutine"],
        cronologia=[{"ruolo": "utente", "testo": "ciao"}],
        chat_id=42,
    )

    risultato, messaggi = handlers.handle_reset_confirmation(profilo, "CONFERMA")

    assert risultato["allergie_intolleranze"] == []
    assert risultato["cronologia"] == []
    assert risultato["onboarding_completato"] is False
    assert risultato["in_attesa_di_conferma_reset"] is False
    assert risultato["chat_id"] == 42
    # Riparte subito dalla prima domanda, senza far riscrivere /start.
    assert "azzerato" in messaggi[0]
    assert "intolleranze" in messaggi[0]


def test_qualsiasi_altra_risposta_annulla_senza_perdere_dati():
    profilo = _profilo_base(in_attesa_di_conferma_reset=True, allergie_intolleranze=["glutine"])

    risultato, messaggi = handlers.handle_reset_confirmation(profilo, "no aspetta")

    assert risultato["in_attesa_di_conferma_reset"] is False
    assert risultato["allergie_intolleranze"] == ["glutine"]
    assert "annullato" in messaggi[0].lower()


def test_la_conferma_ignora_maiuscole_e_spazi():
    profilo = _profilo_base(in_attesa_di_conferma_reset=True, allergie_intolleranze=["glutine"])

    risultato, _ = handlers.handle_reset_confirmation(profilo, "  conferma  ")

    assert risultato["allergie_intolleranze"] == []
