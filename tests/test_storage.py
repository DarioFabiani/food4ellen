import json
from unittest.mock import MagicMock, patch

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
    mock_post.assert_called_once()


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

    args, kwargs = mock_post.call_args
    assert args[0] == f"https://example.upstash.io/set/{storage.PROFILE_KEY}"
    assert json.loads(kwargs["data"]) == {"foo": "bar"}
