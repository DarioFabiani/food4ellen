"""Test di `processa_messaggio`: il percorso unico verso l'agente."""
import copy
from unittest.mock import patch

import pytest

import handlers
import profile_ops
import storage


def _profilo_base(**overrides) -> dict:
    profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    profilo.update(overrides)
    return profilo


def _agente_che_risponde(testo="ok"):
    """Finto agente che lascia il profilo com'è e restituisce un testo."""
    return lambda system, blocchi, profile: (profile, testo)


# --- guardie immagine -----------------------------------------------------


@patch("handlers.claude_client.esegui_agente")
@patch("handlers.claude_client.descrivi_immagine")
def test_rifiuta_un_media_type_non_supportato(mock_descrivi, mock_agente):
    _, messaggi = handlers.processa_messaggio(
        _profilo_base(), None, b"finti-byte", "image/heic"
    )

    mock_descrivi.assert_not_called()
    mock_agente.assert_not_called()
    assert "foto" in messaggi[0].lower()


@patch("handlers.claude_client.esegui_agente")
@patch("handlers.claude_client.descrivi_immagine")
def test_rifiuta_un_immagine_troppo_grande(mock_descrivi, mock_agente):
    immagine = b"x" * (handlers.MAX_IMMAGINE_BYTES + 1)

    _, messaggi = handlers.processa_messaggio(_profilo_base(), None, immagine, "image/jpeg")

    mock_descrivi.assert_not_called()
    mock_agente.assert_not_called()
    assert "grande" in messaggi[0].lower()


@patch("handlers.claude_client.descrivi_immagine", return_value="tipo: menu\n- 'insalata'")
def test_la_foto_viene_convertita_in_testo_prima_di_arrivare_all_agente(mock_descrivi):
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde()
        handlers.processa_messaggio(_profilo_base(), None, b"byte", "image/png")

    mock_descrivi.assert_called_once_with(b"byte", "image/png")
    blocchi = mock_agente.call_args.args[1]
    assert "<foto_non_fidata>" in blocchi
    assert "'insalata'" in blocchi


# --- conferma reset (resta deterministica) --------------------------------


@patch("handlers.claude_client.esegui_agente")
def test_la_conferma_del_reset_non_passa_dall_agente(mock_agente):
    profilo = _profilo_base(in_attesa_di_conferma_reset=True, preferenze=[{"item": "x"}])

    risultato, messaggi = handlers.processa_messaggio(profilo, "CONFERMA")

    mock_agente.assert_not_called()
    assert risultato["preferenze"] == []
    assert "azzerato" in messaggi[0]


@patch("handlers.claude_client.esegui_agente")
def test_qualsiasi_altra_risposta_annulla_il_reset(mock_agente):
    profilo = _profilo_base(in_attesa_di_conferma_reset=True)

    risultato, messaggi = handlers.processa_messaggio(profilo, "no aspetta")

    mock_agente.assert_not_called()
    assert risultato["in_attesa_di_conferma_reset"] is False
    assert messaggi == ["Reset annullato."]


@patch("handlers.claude_client.esegui_agente")
def test_una_foto_durante_la_conferma_del_reset_chiede_di_rispondere_a_parole(mock_agente):
    profilo = _profilo_base(in_attesa_di_conferma_reset=True)

    _, messaggi = handlers.processa_messaggio(profilo, None, b"byte", "image/jpeg")

    mock_agente.assert_not_called()
    assert "CONFERMA" in messaggi[0]


# --- percorso normale -----------------------------------------------------


def test_il_messaggio_e_la_risposta_finiscono_in_cronologia():
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde("Prendi l'insalata.")
        risultato, messaggi = handlers.processa_messaggio(_profilo_base(), "che mangio?")

    assert messaggi == ["Prendi l'insalata."]
    assert risultato["cronologia"] == [
        {"ruolo": "utente", "testo": "che mangio?"},
        {"ruolo": "bot", "testo": "Prendi l'insalata."},
    ]


def test_il_messaggio_corrente_e_gia_in_cronologia_quando_parte_l_agente():
    """L'agente deve vedere il turno appena arrivato, non solo i precedenti."""
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde()
        handlers.processa_messaggio(_profilo_base(), "buonissima")

    blocchi = mock_agente.call_args.args[1]
    assert "'buonissima'" in blocchi


def test_le_modifiche_al_profilo_fatte_dai_tool_vengono_conservate():
    def agente_che_scrive(system, blocchi, profile):
        return {**profile, "onboarding_completato": True}, "fatto"

    with patch("handlers.claude_client.esegui_agente", side_effect=agente_che_scrive):
        risultato, _ = handlers.processa_messaggio(_profilo_base(), "sono celiaca")

    assert risultato["onboarding_completato"] is True


def test_una_foto_senza_testo_registra_comunque_un_turno_in_cronologia():
    with patch("handlers.claude_client.descrivi_immagine", return_value="tipo: altro"):
        with patch("handlers.claude_client.esegui_agente") as mock_agente:
            mock_agente.side_effect = _agente_che_risponde()
            risultato, _ = handlers.processa_messaggio(_profilo_base(), None, b"byte", "image/jpeg")

    assert risultato["cronologia"][0]["ruolo"] == "utente"
    assert "foto" in risultato["cronologia"][0]["testo"]


# --- manutenzione dello storico ------------------------------------------


def _profilo_con_pasti(n: int) -> dict:
    pasti = [
        {
            "id": str(i),
            "data": "2026-07-24",
            "opzioni_presentate": [],
            "scelta_consigliata": f"piatto {i}",
            "scelta_reale": None,
            "feedback": None,
            "gradimento": None,
        }
        for i in range(n)
    ]
    return _profilo_base(pasti_recenti=pasti, riassunto_storico="vecchio")


@patch("handlers.claude_client.update_riassunto_storico", return_value="nuovo riassunto")
def test_oltre_venti_pasti_il_piu_vecchio_viene_archiviato(mock_riassunto):
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde()
        risultato, _ = handlers.processa_messaggio(
            _profilo_con_pasti(profile_ops.MAX_PASTI_RECENTI + 1), "ciao"
        )

    mock_riassunto.assert_called_once()
    assert len(risultato["pasti_recenti"]) == profile_ops.MAX_PASTI_RECENTI
    assert risultato["pasti_recenti"][0]["id"] == "1"
    assert risultato["riassunto_storico"] == "nuovo riassunto"


@patch("handlers.claude_client.update_riassunto_storico")
def test_sotto_soglia_non_si_archivia_nulla(mock_riassunto):
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde()
        handlers.processa_messaggio(_profilo_con_pasti(profile_ops.MAX_PASTI_RECENTI), "ciao")

    mock_riassunto.assert_not_called()


@patch("handlers.claude_client.update_riassunto_storico", side_effect=RuntimeError("giù"))
def test_un_fallimento_dell_archiviazione_non_blocca_la_risposta(mock_riassunto):
    """L'archiviazione è manutenzione: la risposta all'utente deve partire lo stesso."""
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde("eccoti il consiglio")
        risultato, messaggi = handlers.processa_messaggio(
            _profilo_con_pasti(profile_ops.MAX_PASTI_RECENTI + 1), "ciao"
        )

    assert messaggi == ["eccoti il consiglio"]
    assert len(risultato["pasti_recenti"]) == profile_ops.MAX_PASTI_RECENTI + 1


@patch("handlers.claude_client.update_riassunto_storico", side_effect=RuntimeError("giù"))
def test_fallimenti_ripetuti_troncano_comunque_lo_storico(mock_riassunto):
    troppi = profile_ops.MAX_PASTI_RECENTI + 6

    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = _agente_che_risponde()
        risultato, _ = handlers.processa_messaggio(_profilo_con_pasti(troppi), "ciao")

    assert len(risultato["pasti_recenti"]) == troppi - 1
    assert risultato["riassunto_storico"] == "vecchio"


# --- /preferenze ----------------------------------------------------------


def test_preferenze_elenca_allergie_e_gusti():
    profilo = _profilo_base(
        allergie_intolleranze=["glutine"],
        preferenze=[
            {"item": "broccoli", "sentiment": "dislike", "peso": 4, "fonte": "dichiarato", "note": "sempre"}
        ],
    )

    _, messaggi = handlers.handle_preferenze_command(profilo)

    assert "glutine" in messaggi[0]
    assert "broccoli: dislike, peso 4 (sempre)" in messaggi[0]


def test_preferenze_su_profilo_vuoto():
    _, messaggi = handlers.handle_preferenze_command(_profilo_base())

    assert "nessuna" in messaggi[0]
    assert "Nessuna preferenza registrata" in messaggi[0]
