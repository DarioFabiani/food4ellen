import pytest

import storage
import profile_ops


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


CHIAVI_USATE_DA_HANDLERS = {
    "chat_id", "onboarding_completato", "onboarding_step", "allergie_intolleranze",
    "preferenze", "pasti_recenti", "riassunto_storico",
    "in_attesa_di_feedback_per", "in_attesa_di_conferma_reset", "ultimo_update_id",
}


def test_default_profile_copre_le_chiavi_usate_dagli_handler():
    assert CHIAVI_USATE_DA_HANDLERS <= set(profile_ops.DEFAULT_PROFILE)


def test_profilo_vuoto_imposta_il_chat_id():
    assert profile_ops.profilo_vuoto(42)["chat_id"] == 42
    assert profile_ops.profilo_vuoto()["chat_id"] is None


def test_profilo_vuoto_non_condivide_le_liste_con_il_default():
    profilo = profile_ops.profilo_vuoto()
    profilo["preferenze"].append({"item": "x"})

    assert profile_ops.DEFAULT_PROFILE["preferenze"] == []


def test_sblocca_chat_azzera_il_chat_id_senza_mutare_l_originale():
    profilo = _profilo_base(chat_id=42)

    risultato = profile_ops.sblocca_chat(profilo)

    assert risultato["chat_id"] is None
    assert profilo["chat_id"] == 42


@pytest.mark.parametrize(
    "pref",
    [
        None,
        "broccoli",
        {"item": "broccoli", "sentiment": "dislike"},  # peso mancante
        {"item": "broccoli", "peso": 3},  # sentiment mancante
        {"item": "broccoli", "sentiment": "boh", "peso": 3},  # sentiment non valido
        {"item": "broccoli", "sentiment": "dislike", "peso": 0},  # peso fuori range
        {"item": "broccoli", "sentiment": "dislike", "peso": 6},  # peso fuori range
        {"item": "broccoli", "sentiment": "dislike", "peso": True},  # bool non è un peso
        {"item": "broccoli", "sentiment": "dislike", "peso": "3"},  # peso non intero
        {"item": "  ", "sentiment": "dislike", "peso": 3},  # item vuoto
        {"sentiment": "dislike", "peso": 3},  # item mancante
    ],
)
def test_normalizza_preferenza_scarta_le_voci_inutilizzabili(pref):
    assert profile_ops.normalizza_preferenza(pref) is None


def test_normalizza_preferenza_riempie_fonte_e_note_mancanti():
    risultato = profile_ops.normalizza_preferenza(
        {"item": "broccoli", "sentiment": "dislike", "peso": 4}
    )

    assert risultato == {
        "item": "broccoli",
        "sentiment": "dislike",
        "peso": 4,
        "fonte": "inferito",
        "note": None,
    }


def test_normalizza_preferenza_rimuove_le_chiavi_extra():
    risultato = profile_ops.normalizza_preferenza(
        {
            "item": "broccoli",
            "sentiment": "like",
            "peso": 2,
            "fonte": "dichiarato",
            "note": "solo la sera",
            "inventata_dal_modello": "boh",
        }
    )

    assert set(risultato) == {"item", "sentiment", "peso", "fonte", "note"}
    assert risultato["note"] == "solo la sera"


def test_merge_preferenze_scarta_la_nuova_voce_incompleta_e_conserva_quella_valida():
    valida = {"item": "broccoli", "sentiment": "dislike", "peso": 4, "fonte": "dichiarato", "note": None}
    profilo = _profilo_base(preferenze=[valida])

    risultato = profile_ops.merge_preferenze(profilo, [{"item": "pasta", "sentiment": "like"}])

    assert risultato["preferenze"] == [valida]


def test_merge_preferenze_ripara_una_preferenza_gia_corrotta_nel_profilo():
    corrotta = {"item": "pasta", "sentiment": "like"}  # senza peso: irrecuperabile
    incompleta = {"item": "riso", "sentiment": "like", "peso": 3}  # recuperabile
    profilo = _profilo_base(preferenze=[corrotta, incompleta])

    risultato = profile_ops.merge_preferenze(profilo, [])

    assert risultato["preferenze"] == [
        {"item": "riso", "sentiment": "like", "peso": 3, "fonte": "inferito", "note": None}
    ]


def test_normalizza_profilo_aggiunge_le_chiavi_mancanti_senza_toccare_i_valori_presenti():
    dati = {"onboarding_completato": True, "allergie_intolleranze": ["glutine"]}

    risultato = profile_ops.normalizza_profilo(dati)

    assert risultato["onboarding_completato"] is True
    assert risultato["allergie_intolleranze"] == ["glutine"]
    assert risultato["ultimo_update_id"] is None
    assert set(risultato) >= set(profile_ops.DEFAULT_PROFILE)


def test_normalizza_profilo_ripulisce_preferenze_e_tipi_sbagliati():
    dati = {
        "preferenze": [
            {"item": "broccoli", "sentiment": "dislike", "peso": 4},
            {"item": "pasta", "sentiment": "boh", "peso": 2},
        ],
        "pasti_recenti": "non-una-lista",
        "riassunto_storico": 42,
    }

    risultato = profile_ops.normalizza_profilo(dati)

    assert risultato["preferenze"] == [
        {"item": "broccoli", "sentiment": "dislike", "peso": 4, "fonte": "inferito", "note": None}
    ]
    assert risultato["pasti_recenti"] == []
    assert risultato["riassunto_storico"] == ""


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
