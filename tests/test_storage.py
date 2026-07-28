import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import storage

UPSTASH_ENV = {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"}


def _mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_returns_default_when_key_missing_e_chiave_legacy_assente(mock_get, mock_post):
    # Prima GET: chiave per-chat assente. Seconda GET: chiave legacy assente.
    mock_get.side_effect = [_mock_response({"result": None}), _mock_response({"result": None})]
    mock_post.return_value = _mock_response({"result": "OK"})

    profile = storage.load_profile(42)

    assert profile == {**storage.DEFAULT_PROFILE, "chat_id": 42}
    # SET principale + SET di backup (nessuna migrazione, nessun DEL)
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[0][0][0] == f"https://example.upstash.io/set/mensa_bot:profile:42"


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.get")
def test_load_profile_parses_existing_json(mock_get):
    stored = {**storage.DEFAULT_PROFILE, "chat_id": 42, "onboarding_completato": True}
    mock_get.return_value = _mock_response({"result": json.dumps(stored)})

    profile = storage.load_profile(42)

    assert profile["onboarding_completato"] is True
    mock_get.assert_called_once_with(
        "https://example.upstash.io/get/mensa_bot:profile:42", headers=storage._headers(), timeout=10
    )


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
def test_save_profile_posts_json_payload_to_correct_url(mock_post):
    mock_post.return_value = _mock_response({"result": "OK"})

    storage.save_profile(42, {"foo": "bar"})

    args, kwargs = mock_post.call_args_list[0]
    assert args[0] == "https://example.upstash.io/set/mensa_bot:profile:42"
    assert json.loads(kwargs["data"]) == {"foo": "bar"}


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
def test_save_profile_scrive_anche_la_chiave_di_backup_per_chat(mock_post):
    mock_post.return_value = _mock_response({"result": "OK"})

    storage.save_profile(42, {"foo": "bar"})

    assert mock_post.call_count == 2
    url_backup = mock_post.call_args_list[1][0][0]
    assert url_backup.startswith("https://example.upstash.io/set/mensa_bot:profile:backup:42:")
    assert json.loads(mock_post.call_args_list[1][1]["data"]) == {"foo": "bar"}


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
def test_save_profile_non_fallisce_se_il_backup_fallisce(mock_post):
    mock_post.side_effect = [
        _mock_response({"result": "OK"}),
        requests.exceptions.ConnectionError("backup ko"),
    ]

    storage.save_profile(42, {"foo": "bar"})

    assert mock_post.call_count == 2


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_solleva_se_manca_il_campo_result(mock_get, mock_post):
    mock_get.return_value = _mock_response({})

    with pytest.raises(storage.StorageError):
        storage.load_profile(42)

    mock_post.assert_not_called()


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_solleva_se_il_profilo_non_e_un_oggetto(mock_get, mock_post):
    mock_get.return_value = _mock_response({"result": '"non-un-dict"'})

    with pytest.raises(storage.StorageError):
        storage.load_profile(42)

    mock_post.assert_not_called()


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.get")
def test_load_profile_completa_le_chiavi_mancanti_dallo_schema(mock_get):
    stored = {k: v for k, v in storage.DEFAULT_PROFILE.items() if k != "ultimo_update_id"}
    stored["chat_id"] = 42
    stored["onboarding_completato"] = True
    mock_get.return_value = _mock_response({"result": json.dumps(stored)})

    profile = storage.load_profile(42)

    assert profile["ultimo_update_id"] is None
    assert profile["onboarding_completato"] is True


# ---------------------------------------------------------------------------
# Migrazione dalla vecchia chiave fissa (mono-utente) a quella per-chat
# ---------------------------------------------------------------------------


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_migra_il_profilo_legacy_se_il_chat_id_combacia(mock_get, mock_post):
    legacy = {**storage.DEFAULT_PROFILE, "chat_id": 42, "onboarding_completato": True}
    mock_get.side_effect = [
        _mock_response({"result": None}),  # chiave per-chat assente
        _mock_response({"result": json.dumps(legacy)}),  # chiave legacy presente, stesso chat_id
    ]
    mock_post.return_value = _mock_response({"result": "OK"})

    profile = storage.load_profile(42)

    assert profile["chat_id"] == 42
    assert profile["onboarding_completato"] is True
    # SET per-chat + SET backup + DEL della chiave legacy
    assert mock_post.call_count == 3
    assert mock_post.call_args_list[0][0][0] == "https://example.upstash.io/set/mensa_bot:profile:42"
    assert mock_post.call_args_list[2][0][0] == f"https://example.upstash.io/del/{storage.LEGACY_PROFILE_KEY}"


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_non_migra_se_il_chat_id_legacy_non_combacia(mock_get, mock_post):
    legacy = {**storage.DEFAULT_PROFILE, "chat_id": 999}
    mock_get.side_effect = [
        _mock_response({"result": None}),
        _mock_response({"result": json.dumps(legacy)}),
    ]
    mock_post.return_value = _mock_response({"result": "OK"})

    profile = storage.load_profile(42)

    assert profile == {**storage.DEFAULT_PROFILE, "chat_id": 42}
    # Nessuna migrazione: solo SET per-chat + SET backup del profilo vuoto, nessun DEL.
    assert mock_post.call_count == 2
    assert all("/del/" not in call[0][0] for call in mock_post.call_args_list)


@patch.dict("os.environ", UPSTASH_ENV)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_non_migra_se_la_chiave_legacy_e_assente(mock_get, mock_post):
    mock_get.side_effect = [
        _mock_response({"result": None}),
        _mock_response({"result": None}),
    ]
    mock_post.return_value = _mock_response({"result": "OK"})

    profile = storage.load_profile(42)

    assert profile == {**storage.DEFAULT_PROFILE, "chat_id": 42}
    assert mock_post.call_count == 2
    assert all("/del/" not in call[0][0] for call in mock_post.call_args_list)
