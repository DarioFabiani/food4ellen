"""Regressione sul caso che ha motivato il refactor.

L'utente scrive «Perfetta, ho assaggiato una ortolana ... e mi è piaciuta
molto.» dopo che il bot le ha consigliato la pizza ortolana. Il flusso
deterministico lo trattava come un menu di una riga e "consigliava" di nuovo la
stessa pizza, senza imparare nulla.

Qui si verificano le due cose che devono valere perché non si ripeta:
1. l'agente riceve i dati per capirlo (id del pasto in sospeso e il proprio
   messaggio precedente in cronologia);
2. quando decide che è un feedback, il profilo lo registra davvero — e nessun
   pasto nuovo viene creato.
"""
import copy
from unittest.mock import MagicMock, patch

import claude_client
import handlers
import storage
from conftest import risposta, risposta_testo, blocco_tool_use

CONSIGLIO_DEL_BOT = (
    "Oggi ti consiglio la pizza ortolana con mozzarella, zucchine e melanzane: "
    "ti sei detta amante della pizza e non contiene fagioli, quindi sei a posto "
    "con l'intolleranza!"
)
MESSAGGIO_UTENTE = (
    "Perfetta, ho assaggiato una ortolana (mozzarella, zucchine e melanzane) e mi è piaciuta molto."
)

PREFERENZA_APPRESA = {
    "item": "pizza ortolana",
    "sentiment": "like",
    "peso": 4,
    "fonte": "inferito",
    "note": None,
}


def _profilo_dello_screenshot() -> dict:
    profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    profilo.update(
        onboarding_completato=True,
        allergie_intolleranze=["fagioli"],
        preferenze=[
            {"item": "pizza", "sentiment": "like", "peso": 4, "fonte": "dichiarato", "note": None}
        ],
        pasti_recenti=[
            {
                "id": "pasto-ortolana",
                "data": "2026-07-26",
                "opzioni_presentate": ["pizza ortolana", "margherita"],
                "scelta_consigliata": "pizza ortolana",
                "scelta_reale": None,
                "feedback": None,
                "gradimento": None,
            }
        ],
        cronologia=[{"ruolo": "bot", "testo": CONSIGLIO_DEL_BOT}],
    )
    return profilo


def test_l_agente_riceve_l_id_del_pasto_e_il_proprio_messaggio_precedente():
    """Senza questi due dati il modello non può che tirare a indovinare."""
    with patch("handlers.claude_client.esegui_agente") as mock_agente:
        mock_agente.side_effect = lambda system, blocchi, profile: (profile, "ok")
        handlers.processa_messaggio(_profilo_dello_screenshot(), MESSAGGIO_UTENTE)

    blocchi = mock_agente.call_args.args[1]
    assert "pasto-ortolana" in blocchi
    assert "gradimento: non ancora dato" in blocchi
    assert "pizza ortolana" in blocchi
    assert "<conversazione_recente>" in blocchi
    assert CONSIGLIO_DEL_BOT[:40] in blocchi


def test_il_feedback_spontaneo_aggiorna_il_pasto_e_le_preferenze():
    """Il modello emette i due tool in parallelo: feedback + preferenza appresa."""
    client = MagicMock()
    client.messages.create.side_effect = [
        risposta(
            blocco_tool_use(
                "registra_feedback_pasto",
                {
                    "pasto_id": "pasto-ortolana",
                    "gradimento": "positivo",
                    "scelta_reale": None,
                    "testo_feedback": MESSAGGIO_UTENTE,
                },
                "tu_1",
            ),
            blocco_tool_use("salva_preferenze", {"preferenze": [PREFERENZA_APPRESA]}, "tu_2"),
            stop_reason="tool_use",
        ),
        risposta_testo("Che bello, me lo segno!"),
    ]

    with patch.object(claude_client, "_get_client", return_value=client):
        profilo, messaggi = handlers.processa_messaggio(
            _profilo_dello_screenshot(), MESSAGGIO_UTENTE
        )

    pasto = profilo["pasti_recenti"][0]
    assert pasto["gradimento"] == "positivo"
    assert pasto["feedback"] == MESSAGGIO_UTENTE

    # Nessun pasto nuovo: il messaggio non era un menu.
    assert len(profilo["pasti_recenti"]) == 1

    assert PREFERENZA_APPRESA in profilo["preferenze"]
    assert messaggi == ["Che bello, me lo segno!"]
