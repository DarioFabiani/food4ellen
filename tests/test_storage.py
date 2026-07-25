import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import storage


def _mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_returns_default_when_key_missing(mock_get, mock_post):
    mock_get.return_value = _mock_response({"result": None})
    mock_post.return_value = _mock_response({"result": "OK"})

    profile = storage.load_profile()

    assert profile == storage.DEFAULT_PROFILE
    # SET principale + SET di backup
    assert mock_post.call_count == 2


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.get")
def test_load_profile_parses_existing_json(mock_get):
    stored = {**storage.DEFAULT_PROFILE, "onboarding_completato": True}
    mock_get.return_value = _mock_response({"result": json.dumps(stored)})

    profile = storage.load_profile()

    assert profile["onboarding_completato"] is True


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.post")
def test_save_profile_posts_json_payload_to_correct_url(mock_post):
    mock_post.return_value = _mock_response({"result": "OK"})

    storage.save_profile({"foo": "bar"})

    args, kwargs = mock_post.call_args_list[0]
    assert args[0] == f"https://example.upstash.io/set/{storage.PROFILE_KEY}"
    assert json.loads(kwargs["data"]) == {"foo": "bar"}


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.post")
def test_save_profile_scrive_anche_la_chiave_di_backup(mock_post):
    mock_post.return_value = _mock_response({"result": "OK"})

    storage.save_profile({"foo": "bar"})

    assert mock_post.call_count == 2
    url_backup = mock_post.call_args_list[1][0][0]
    assert url_backup.startswith("https://example.upstash.io/set/mensa_bot:profile:backup:")
    assert json.loads(mock_post.call_args_list[1][1]["data"]) == {"foo": "bar"}


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.post")
def test_save_profile_non_fallisce_se_il_backup_fallisce(mock_post):
    mock_post.side_effect = [
        _mock_response({"result": "OK"}),
        requests.exceptions.ConnectionError("backup ko"),
    ]

    storage.save_profile({"foo": "bar"})

    assert mock_post.call_count == 2


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_solleva_se_manca_il_campo_result(mock_get, mock_post):
    mock_get.return_value = _mock_response({})

    with pytest.raises(storage.StorageError):
        storage.load_profile()

    mock_post.assert_not_called()


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.post")
@patch("storage.requests.get")
def test_load_profile_solleva_se_il_profilo_non_e_un_oggetto(mock_get, mock_post):
    mock_get.return_value = _mock_response({"result": '"non-un-dict"'})

    with pytest.raises(storage.StorageError):
        storage.load_profile()

    mock_post.assert_not_called()


@patch.dict(
    "os.environ",
    {"UPSTASH_REDIS_REST_URL": "https://example.upstash.io", "UPSTASH_REDIS_REST_TOKEN": "token123"},
)
@patch("storage.requests.get")
def test_load_profile_completa_le_chiavi_mancanti_dallo_schema(mock_get):
    stored = {k: v for k, v in storage.DEFAULT_PROFILE.items() if k != "ultimo_update_id"}
    stored["onboarding_completato"] = True
    mock_get.return_value = _mock_response({"result": json.dumps(stored)})

    profile = storage.load_profile()

    assert profile["ultimo_update_id"] is None
    assert profile["onboarding_completato"] is True
