"""Test del dispatcher dei tool: puro, senza mock e senza rete."""
import copy

import pytest

import agent_tools
import profile_ops
import storage


def _profilo_base(**overrides) -> dict:
    profilo = copy.deepcopy(storage.DEFAULT_PROFILE)
    profilo.update(overrides)
    return profilo


def _pasto(pasto_id="abc", scelta="pizza ortolana", **overrides) -> dict:
    pasto = {
        "id": pasto_id,
        "data": "2026-07-24",
        "opzioni_presentate": ["pizza ortolana", "insalata"],
        "scelta_consigliata": scelta,
        "scelta_reale": None,
        "feedback": None,
        "gradimento": None,
    }
    pasto.update(overrides)
    return pasto


PREFERENZA_VALIDA = {
    "item": "zucchine",
    "sentiment": "like",
    "peso": 4,
    "fonte": "inferito",
    "note": None,
}


# --- definizioni ---------------------------------------------------------


def test_ogni_tool_ha_nome_descrizione_e_schema_stretto():
    for tool in agent_tools.TOOL_DEFINITIONS:
        assert tool["name"] and tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        # Con strict: True l'API pretende che ogni proprietà sia in "required".
        assert set(schema["required"]) == set(schema["properties"])


def test_ogni_tool_dichiarato_ha_un_implementazione():
    assert agent_tools.NOMI_TOOL == set(agent_tools._DISPATCH)


# --- salva_preferenze ----------------------------------------------------


def test_salva_preferenze_aggiunge_le_voci_valide():
    profilo = _profilo_base()

    risultato, testo, is_error = agent_tools.esegui_tool(
        "salva_preferenze", {"preferenze": [PREFERENZA_VALIDA]}, profilo
    )

    assert is_error is False
    assert risultato["preferenze"] == [PREFERENZA_VALIDA]
    assert "zucchine" in testo


def test_salva_preferenze_non_muta_il_profilo_in_ingresso():
    profilo = _profilo_base()

    agent_tools.esegui_tool("salva_preferenze", {"preferenze": [PREFERENZA_VALIDA]}, profilo)

    assert profilo["preferenze"] == []


def test_salva_preferenze_registra_le_valide_e_segnala_le_scartate():
    profilo = _profilo_base()

    risultato, testo, is_error = agent_tools.esegui_tool(
        "salva_preferenze",
        {"preferenze": [PREFERENZA_VALIDA, {"item": "pasta", "sentiment": "boh", "peso": 9}]},
        profilo,
    )

    assert is_error is False
    assert [p["item"] for p in risultato["preferenze"]] == ["zucchine"]
    assert "scartate" in testo


def test_salva_preferenze_e_un_errore_se_nessuna_voce_e_utilizzabile():
    profilo = _profilo_base()

    risultato, testo, is_error = agent_tools.esegui_tool(
        "salva_preferenze", {"preferenze": [{"item": "pasta"}]}, profilo
    )

    assert is_error is True
    assert risultato["preferenze"] == []
    assert "peso" in testo


@pytest.mark.parametrize("argomenti", [{}, {"preferenze": []}, {"preferenze": "zucchine"}])
def test_salva_preferenze_rifiuta_gli_argomenti_malformati(argomenti):
    _, _, is_error = agent_tools.esegui_tool("salva_preferenze", argomenti, _profilo_base())
    assert is_error is True


# --- aggiorna_allergie_intolleranze --------------------------------------


def test_aggiorna_allergie_aggiunge_e_chiede_di_confermare_all_utente():
    profilo = _profilo_base(allergie_intolleranze=["glutine"])

    risultato, testo, is_error = agent_tools.esegui_tool(
        "aggiorna_allergie_intolleranze", {"voci": ["lattosio"], "modo": "aggiungi"}, profilo
    )

    assert is_error is False
    assert risultato["allergie_intolleranze"] == ["glutine", "lattosio"]
    # Un vincolo assoluto non può cambiare in silenzio.
    assert "Conferma" in testo


def test_aggiorna_allergie_sostituisce_l_elenco_intero():
    profilo = _profilo_base(allergie_intolleranze=["glutine", "lattosio"])

    risultato, _, is_error = agent_tools.esegui_tool(
        "aggiorna_allergie_intolleranze", {"voci": ["noci"], "modo": "sostituisci"}, profilo
    )

    assert is_error is False
    assert risultato["allergie_intolleranze"] == ["noci"]


def test_aggiorna_allergie_segnala_quando_non_cambia_nulla():
    profilo = _profilo_base(allergie_intolleranze=["glutine"])

    _, testo, is_error = agent_tools.esegui_tool(
        "aggiorna_allergie_intolleranze", {"voci": ["Glutine"], "modo": "aggiungi"}, profilo
    )

    assert is_error is False
    assert "invariato" in testo


@pytest.mark.parametrize(
    "argomenti",
    [{"voci": "glutine", "modo": "aggiungi"}, {"voci": ["noci"], "modo": "cancella"}],
)
def test_aggiorna_allergie_rifiuta_gli_argomenti_malformati(argomenti):
    _, _, is_error = agent_tools.esegui_tool(
        "aggiorna_allergie_intolleranze", argomenti, _profilo_base()
    )
    assert is_error is True


# --- registra_raccomandazione --------------------------------------------


def test_registra_raccomandazione_salva_il_pasto_e_restituisce_l_id():
    profilo = _profilo_base()

    risultato, testo, is_error = agent_tools.esegui_tool(
        "registra_raccomandazione",
        {"opzioni_presentate": ["pizza ortolana", "insalata"], "scelta_consigliata": "insalata"},
        profilo,
    )

    assert is_error is False
    assert len(risultato["pasti_recenti"]) == 1
    assert risultato["pasti_recenti"][0]["id"] in testo


def test_registra_raccomandazione_blocca_un_opzione_che_contiene_un_allergene():
    profilo = _profilo_base(allergie_intolleranze=["glutine"])

    risultato, testo, is_error = agent_tools.esegui_tool(
        "registra_raccomandazione",
        {"opzioni_presentate": ["pasta al glutine"], "scelta_consigliata": "pasta al glutine"},
        profilo,
    )

    assert is_error is True
    assert risultato["pasti_recenti"] == []
    assert "vincolo assoluto" in testo


def test_registra_raccomandazione_non_blocca_su_una_sottostringa_di_un_altra_parola():
    """Il match è su confine di parola: 'noci' non deve far scattare 'nocino'."""
    profilo = _profilo_base(allergie_intolleranze=["noci"])

    _, _, is_error = agent_tools.esegui_tool(
        "registra_raccomandazione",
        {"opzioni_presentate": ["gelato al nocino"], "scelta_consigliata": "gelato al nocino"},
        profilo,
    )

    assert is_error is False


def test_registra_raccomandazione_scarta_le_opzioni_non_stringa():
    profilo = _profilo_base()

    risultato, _, is_error = agent_tools.esegui_tool(
        "registra_raccomandazione",
        {"opzioni_presentate": ["insalata", None, "  "], "scelta_consigliata": "insalata"},
        profilo,
    )

    assert is_error is False
    assert risultato["pasti_recenti"][0]["opzioni_presentate"] == ["insalata"]


@pytest.mark.parametrize(
    "argomenti",
    [
        {"opzioni_presentate": ["a"], "scelta_consigliata": "  "},
        {"opzioni_presentate": "a", "scelta_consigliata": "a"},
        {"scelta_consigliata": "a"},
    ],
)
def test_registra_raccomandazione_rifiuta_gli_argomenti_malformati(argomenti):
    _, _, is_error = agent_tools.esegui_tool("registra_raccomandazione", argomenti, _profilo_base())
    assert is_error is True


# --- registra_feedback_pasto ---------------------------------------------


def test_registra_feedback_aggiorna_il_pasto():
    profilo = _profilo_base(pasti_recenti=[_pasto()])

    risultato, _, is_error = agent_tools.esegui_tool(
        "registra_feedback_pasto",
        {
            "pasto_id": "abc",
            "gradimento": "positivo",
            "scelta_reale": None,
            "testo_feedback": "mi è piaciuta molto",
        },
        profilo,
    )

    assert is_error is False
    pasto = risultato["pasti_recenti"][0]
    assert pasto["gradimento"] == "positivo"
    assert pasto["feedback"] == "mi è piaciuta molto"
    assert pasto["scelta_reale"] is None


def test_registra_feedback_valorizza_la_scelta_reale_quando_diversa():
    profilo = _profilo_base(pasti_recenti=[_pasto()])

    risultato, _, _ = agent_tools.esegui_tool(
        "registra_feedback_pasto",
        {
            "pasto_id": "abc",
            "gradimento": "neutro",
            "scelta_reale": "insalata",
            "testo_feedback": "ho preso l'insalata",
        },
        profilo,
    )

    assert risultato["pasti_recenti"][0]["scelta_reale"] == "insalata"


def test_registra_feedback_su_id_inesistente_elenca_quelli_validi():
    profilo = _profilo_base(pasti_recenti=[_pasto("abc")])

    risultato, testo, is_error = agent_tools.esegui_tool(
        "registra_feedback_pasto",
        {
            "pasto_id": "non-esiste",
            "gradimento": "positivo",
            "scelta_reale": None,
            "testo_feedback": "buona",
        },
        profilo,
    )

    assert is_error is True
    assert "abc" in testo
    assert risultato["pasti_recenti"][0]["gradimento"] is None


def test_registra_feedback_rifiuta_un_gradimento_non_valido():
    profilo = _profilo_base(pasti_recenti=[_pasto()])

    _, _, is_error = agent_tools.esegui_tool(
        "registra_feedback_pasto",
        {"pasto_id": "abc", "gradimento": "boh", "scelta_reale": None, "testo_feedback": "x"},
        profilo,
    )

    assert is_error is True


def test_registra_feedback_non_tocca_le_preferenze():
    """Le preferenze hanno un tool dedicato: questo registra solo il pasto."""
    profilo = _profilo_base(pasti_recenti=[_pasto()], preferenze=[PREFERENZA_VALIDA])

    risultato, _, _ = agent_tools.esegui_tool(
        "registra_feedback_pasto",
        {"pasto_id": "abc", "gradimento": "positivo", "scelta_reale": None, "testo_feedback": "ok"},
        profilo,
    )

    assert risultato["preferenze"] == [PREFERENZA_VALIDA]


# --- segna_onboarding_completato -----------------------------------------


def test_segna_onboarding_completato_alza_il_flag():
    profilo = _profilo_base(onboarding_completato=False)

    risultato, _, is_error = agent_tools.esegui_tool(
        "segna_onboarding_completato", {}, profilo
    )

    assert is_error is False
    assert risultato["onboarding_completato"] is True
    assert profilo["onboarding_completato"] is False


def test_segna_onboarding_completato_e_idempotente():
    profilo = _profilo_base(onboarding_completato=True)

    risultato, testo, is_error = agent_tools.esegui_tool(
        "segna_onboarding_completato", {}, profilo
    )

    assert is_error is False
    assert risultato["onboarding_completato"] is True
    assert "già" in testo


# --- robustezza del dispatcher -------------------------------------------


def test_tool_sconosciuto_torna_errore_senza_sollevare():
    profilo = _profilo_base()

    risultato, testo, is_error = agent_tools.esegui_tool("cancella_tutto", {}, profilo)

    assert is_error is True
    assert risultato == profilo
    assert "sconosciuto" in testo


def test_argomenti_non_oggetto_tornano_errore():
    _, _, is_error = agent_tools.esegui_tool("salva_preferenze", ["zucchine"], _profilo_base())
    assert is_error is True


def test_un_eccezione_nel_tool_diventa_un_tool_result_di_errore(monkeypatch):
    def esplode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(profile_ops, "merge_preferenze", esplode)

    _, testo, is_error = agent_tools.esegui_tool(
        "salva_preferenze", {"preferenze": [PREFERENZA_VALIDA]}, _profilo_base()
    )

    assert is_error is True
    assert "boom" in testo
