import storage
import profile_ops


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


def test_merge_preferenze_aggiunge_nuove_voci():
    profilo = _profilo_base(preferenze=[])
    nuove = [{"item": "broccoli", "sentiment": "dislike", "peso": 4, "fonte": "dichiarato", "note": None}]

    risultato = profile_ops.merge_preferenze(profilo, nuove)

    assert risultato["preferenze"] == nuove


def test_merge_preferenze_sovrascrive_voce_esistente():
    esistente = {"item": "broccoli", "sentiment": "dislike", "peso": 2, "fonte": "dichiarato", "note": None}
    profilo = _profilo_base(preferenze=[esistente])
    aggiornata = {"item": "broccoli", "sentiment": "dislike", "peso": 5, "fonte": "inferito", "note": "confermato più volte"}

    risultato = profile_ops.merge_preferenze(profilo, [aggiornata])

    assert risultato["preferenze"] == [aggiornata]


def test_record_new_meal_aggiunge_pasto_con_id_e_feedback_null():
    profilo = _profilo_base()

    risultato = profile_ops.record_new_meal(profilo, ["pasta al forno", "insalata"], "insalata")

    assert len(risultato["pasti_recenti"]) == 1
    pasto = risultato["pasti_recenti"][0]
    assert pasto["scelta_consigliata"] == "insalata"
    assert pasto["opzioni_presentate"] == ["pasta al forno", "insalata"]
    assert pasto["feedback"] is None
    assert pasto["gradimento"] is None
    assert pasto["scelta_reale"] is None
    assert "id" in pasto and "data" in pasto


def test_find_pasto_in_attesa_di_feedback_trova_il_primo_senza_feedback():
    valutato = {"id": "1", "feedback": "buono", "gradimento": "positivo"}
    non_valutato = {"id": "2", "feedback": None, "gradimento": None}
    profilo = _profilo_base(pasti_recenti=[valutato, non_valutato])

    risultato = profile_ops.find_pasto_in_attesa_di_feedback(profilo)

    assert risultato["id"] == "2"


def test_find_pasto_in_attesa_di_feedback_preferisce_il_piu_recente_tra_due_non_valutati():
    piu_vecchio = {"id": "1", "feedback": None, "gradimento": None}
    piu_recente = {"id": "2", "feedback": None, "gradimento": None}
    profilo = _profilo_base(pasti_recenti=[piu_vecchio, piu_recente])

    risultato = profile_ops.find_pasto_in_attesa_di_feedback(profilo)

    assert risultato["id"] == "2"


def test_find_pasto_in_attesa_di_feedback_restituisce_none_se_tutti_valutati():
    valutato = {"id": "1", "feedback": "buono", "gradimento": "positivo"}
    profilo = _profilo_base(pasti_recenti=[valutato])

    assert profile_ops.find_pasto_in_attesa_di_feedback(profilo) is None


def test_apply_feedback_aggiorna_pasto_e_preferenze():
    pasto = {
        "id": "abc",
        "data": "2026-07-09",
        "opzioni_presentate": ["pasta", "insalata"],
        "scelta_consigliata": "insalata",
        "scelta_reale": None,
        "feedback": None,
        "gradimento": None,
    }
    profilo = _profilo_base(pasti_recenti=[pasto], preferenze=[])
    nuove_preferenze = [{"item": "insalata", "sentiment": "like", "peso": 3, "fonte": "inferito", "note": None}]

    risultato = profile_ops.apply_feedback(
        profilo, "abc", "positivo", "pasta", "buona ma avrei preferito la pasta", nuove_preferenze
    )

    pasto_aggiornato = risultato["pasti_recenti"][0]
    assert pasto_aggiornato["gradimento"] == "positivo"
    assert pasto_aggiornato["scelta_reale"] == "pasta"
    assert pasto_aggiornato["feedback"] == "buona ma avrei preferito la pasta"
    assert risultato["preferenze"] == nuove_preferenze


def test_pasto_piu_vecchio_da_archiviare_none_se_sotto_soglia():
    profilo = _profilo_base(pasti_recenti=[{"id": str(i)} for i in range(20)])
    assert profile_ops.pasto_piu_vecchio_da_archiviare(profilo) is None


def test_pasto_piu_vecchio_da_archiviare_restituisce_il_primo_se_sopra_soglia():
    pasti = [{"id": str(i)} for i in range(21)]
    profilo = _profilo_base(pasti_recenti=pasti)
    assert profile_ops.pasto_piu_vecchio_da_archiviare(profilo) == pasti[0]


def test_archivia_pasto_piu_vecchio_rimuove_il_primo_e_aggiorna_riassunto():
    pasti = [{"id": str(i)} for i in range(21)]
    profilo = _profilo_base(pasti_recenti=pasti, riassunto_storico="vecchio riassunto")

    risultato = profile_ops.archivia_pasto_piu_vecchio(profilo, "nuovo riassunto")

    assert len(risultato["pasti_recenti"]) == 20
    assert risultato["pasti_recenti"][0]["id"] == "1"
    assert risultato["riassunto_storico"] == "nuovo riassunto"
