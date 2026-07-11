from unittest.mock import patch

import storage
import handlers


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


def test_handle_menu_con_lista_vuota_chiede_di_riprovare():
    profilo = _profilo_base()

    _, messaggi = handlers.handle_menu(profilo, [])

    assert "riprovare" in messaggi[0].lower() or "opzioni" in messaggi[0].lower()


@patch("handlers.claude_client.get_recommendation")
def test_handle_menu_registra_il_pasto_e_restituisce_il_messaggio(mock_get_recommendation):
    mock_get_recommendation.return_value = {
        "scelta_consigliata": "insalata",
        "messaggio": "Prendi l'insalata, è la più leggera!",
        "alternativa": None,
    }
    profilo = _profilo_base()

    profilo_aggiornato, messaggi = handlers.handle_menu(profilo, ["pasta al forno", "insalata"])

    assert messaggi == ["Prendi l'insalata, è la più leggera!"]
    assert len(profilo_aggiornato["pasti_recenti"]) == 1
    assert profilo_aggiornato["pasti_recenti"][0]["scelta_consigliata"] == "insalata"


@patch("handlers.claude_client.update_riassunto_storico")
@patch("handlers.claude_client.get_recommendation")
def test_handle_menu_archivia_il_pasto_piu_vecchio_oltre_20(mock_get_recommendation, mock_update_riassunto):
    mock_get_recommendation.return_value = {
        "scelta_consigliata": "insalata",
        "messaggio": "Prendi l'insalata!",
        "alternativa": None,
    }
    mock_update_riassunto.return_value = "Pattern: preferisce pasti leggeri."
    pasti_esistenti = [{"id": str(i), "data": "2026-01-01", "scelta_consigliata": "x", "scelta_reale": None, "gradimento": None, "feedback": None, "opzioni_presentate": ["x"]} for i in range(20)]
    profilo = _profilo_base(pasti_recenti=pasti_esistenti)

    profilo_aggiornato, _ = handlers.handle_menu(profilo, ["insalata"])

    assert len(profilo_aggiornato["pasti_recenti"]) == 20
    assert profilo_aggiornato["riassunto_storico"] == "Pattern: preferisce pasti leggeri."
    mock_update_riassunto.assert_called_once()
