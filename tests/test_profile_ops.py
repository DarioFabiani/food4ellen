import pytest

import storage
import profile_ops


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


CHIAVI_USATE_DA_HANDLERS = {
    "chat_id", "onboarding_completato", "allergie_intolleranze",
    "preferenze", "pasti_recenti", "riassunto_storico", "cronologia",
    "in_attesa_di_conferma_reset", "ultimo_update_id",
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


@pytest.mark.parametrize(
    "dati, campo, atteso",
    [
        ({"ultimo_update_id": "100"}, "ultimo_update_id", None),
        ({"ultimo_update_id": 12.5}, "ultimo_update_id", None),
        ({"ultimo_update_id": True}, "ultimo_update_id", None),
        ({"chat_id": "42"}, "chat_id", None),
        ({"chat_id": True}, "chat_id", None),
        ({"onboarding_completato": "si"}, "onboarding_completato", False),
        ({"onboarding_completato": 1}, "onboarding_completato", False),
    ],
)
def test_normalizza_profilo_ripristina_gli_scalari_col_tipo_sbagliato(dati, campo, atteso):
    risultato = profile_ops.normalizza_profilo(dati)
    assert risultato[campo] == atteso
    if campo != "onboarding_completato":
        assert not isinstance(risultato[campo], bool)


def test_normalizza_profilo_conserva_gli_scalari_validi():
    risultato = profile_ops.normalizza_profilo(
        {"ultimo_update_id": 987, "onboarding_completato": True, "chat_id": -100123}
    )
    assert risultato["ultimo_update_id"] == 987
    assert risultato["onboarding_completato"] is True
    assert risultato["chat_id"] == -100123


def test_normalizza_profilo_scarta_i_campi_non_piu_nello_schema():
    """I campi rimossi dallo schema devono sparire dal blob salvato, altrimenti
    si trascinerebbero dietro per sempre."""
    risultato = profile_ops.normalizza_profilo(
        {"onboarding_step": 3, "in_attesa_di_feedback_per": "abc", "roba_a_caso": 1}
    )

    assert set(risultato) == set(profile_ops.DEFAULT_PROFILE)


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

    risultato, pasto_id = profile_ops.record_new_meal(
        profilo, ["pasta al forno", "insalata"], "insalata"
    )

    assert len(risultato["pasti_recenti"]) == 1
    pasto = risultato["pasti_recenti"][0]
    assert pasto["id"] == pasto_id
    assert pasto["scelta_consigliata"] == "insalata"
    assert pasto["opzioni_presentate"] == ["pasta al forno", "insalata"]
    assert pasto["feedback"] is None
    assert pasto["gradimento"] is None
    assert pasto["scelta_reale"] is None
    assert "data" in pasto





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


# --- aggiorna_allergie ---------------------------------------------------


def test_aggiorna_allergie_aggiunge_mantenendo_le_esistenti():
    profilo = _profilo_base(allergie_intolleranze=["glutine"])

    risultato, finale = profile_ops.aggiorna_allergie(profilo, ["lattosio"], "aggiungi")

    assert finale == ["glutine", "lattosio"]
    assert risultato["allergie_intolleranze"] == finale
    assert profilo["allergie_intolleranze"] == ["glutine"]


def test_aggiorna_allergie_sostituisce_scarta_le_precedenti():
    profilo = _profilo_base(allergie_intolleranze=["glutine", "lattosio"])

    _, finale = profile_ops.aggiorna_allergie(profilo, ["noci"], "sostituisci")

    assert finale == ["noci"]


def test_aggiorna_allergie_deduplica_ignorando_maiuscole_e_spazi():
    profilo = _profilo_base(allergie_intolleranze=["glutine"])

    _, finale = profile_ops.aggiorna_allergie(profilo, ["  Glutine ", "lattosio", "lattosio"])

    assert finale == ["glutine", "lattosio"]


@pytest.mark.parametrize("voce", ["", "   ", None, 42, ["glutine"]])
def test_aggiorna_allergie_scarta_le_voci_inutilizzabili(voce):
    profilo = _profilo_base(allergie_intolleranze=[])

    _, finale = profile_ops.aggiorna_allergie(profilo, [voce, "noci"])

    assert finale == ["noci"]


# --- cronologia ----------------------------------------------------------


def test_aggiungi_a_cronologia_appende_senza_mutare_l_originale():
    profilo = _profilo_base(cronologia=[])

    risultato = profile_ops.aggiungi_a_cronologia(profilo, "utente", "ciao")

    assert risultato["cronologia"] == [{"ruolo": "utente", "testo": "ciao"}]
    assert profilo["cronologia"] == []


def test_aggiungi_a_cronologia_tronca_alla_finestra_massima():
    profilo = _profilo_base(cronologia=[])

    for i in range(profile_ops.MAX_CRONOLOGIA + 5):
        profilo = profile_ops.aggiungi_a_cronologia(profilo, "utente", f"messaggio {i}")

    assert len(profilo["cronologia"]) == profile_ops.MAX_CRONOLOGIA
    assert profilo["cronologia"][-1]["testo"] == f"messaggio {profile_ops.MAX_CRONOLOGIA + 4}"
    assert profilo["cronologia"][0]["testo"] == "messaggio 5"


def test_aggiungi_a_cronologia_tronca_i_testi_lunghi():
    profilo = _profilo_base(cronologia=[])

    risultato = profile_ops.aggiungi_a_cronologia(profilo, "bot", "x" * 5000)

    assert len(risultato["cronologia"][0]["testo"]) == profile_ops.MAX_CARATTERI_TURNO


@pytest.mark.parametrize(
    "ruolo, testo",
    [("sistema", "ciao"), ("utente", ""), ("utente", "   "), ("utente", None), (None, "ciao")],
)
def test_aggiungi_a_cronologia_ignora_i_turni_inutilizzabili(ruolo, testo):
    profilo = _profilo_base(cronologia=[])

    risultato = profile_ops.aggiungi_a_cronologia(profilo, ruolo, testo)

    assert risultato["cronologia"] == []


def test_normalizza_profilo_scarta_i_turni_malformati():
    risultato = profile_ops.normalizza_profilo(
        {
            "cronologia": [
                {"ruolo": "utente", "testo": "buono"},
                {"ruolo": "sistema", "testo": "ruolo non valido"},
                "non un dict",
                {"ruolo": "bot"},
            ]
        }
    )

    assert risultato["cronologia"] == [{"ruolo": "utente", "testo": "buono"}]


def test_normalizza_profilo_riapplica_il_tetto_della_cronologia_in_lettura():
    troppi = [{"ruolo": "utente", "testo": f"m{i}"} for i in range(profile_ops.MAX_CRONOLOGIA + 10)]

    risultato = profile_ops.normalizza_profilo({"cronologia": troppi})

    assert len(risultato["cronologia"]) == profile_ops.MAX_CRONOLOGIA
    assert risultato["cronologia"][-1]["testo"] == troppi[-1]["testo"]


def test_normalizza_profilo_ripristina_la_cronologia_se_non_e_una_lista():
    assert profile_ops.normalizza_profilo({"cronologia": "boh"})["cronologia"] == []
