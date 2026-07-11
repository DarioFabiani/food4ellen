import prompts


def test_build_menu_vision_user_text_non_vuoto():
    assert "JSON" in prompts.build_menu_vision_user_text()


def test_build_feedback_user_prompt_include_dati_pasto_e_feedback():
    pasto = {
        "data": "2026-07-09",
        "scelta_consigliata": "insalata",
        "opzioni_presentate": ["pasta", "insalata"],
    }
    testo = prompts.build_feedback_user_prompt(pasto, [], "era troppo scondita")

    assert "insalata" in testo
    assert "era troppo scondita" in testo


def test_build_summary_update_prompt_gestisce_riassunto_vuoto():
    pasto = {
        "data": "2026-07-09",
        "scelta_consigliata": "insalata",
        "scelta_reale": None,
        "gradimento": "positivo",
        "feedback": None,
    }
    testo = prompts.build_summary_update_prompt("", pasto)

    assert "vuoto" in testo
    assert "insalata" in testo
