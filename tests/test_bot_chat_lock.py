# tests/test_bot_chat_lock.py
from unittest.mock import patch

import storage
import bot


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


@patch.dict("os.environ", {"ALLOWED_CHAT_ID": "12345"}, clear=False)
def test_chat_consentita_con_allowed_chat_id_configurato():
    profilo = _profilo_base(chat_id=None)

    assert bot._chat_consentita(profilo, 12345) is True
    assert bot._chat_consentita(profilo, 99999) is False


@patch.dict("os.environ", {}, clear=True)
def test_chat_consentita_senza_allowed_chat_id_e_senza_chat_id_registrato():
    profilo = _profilo_base(chat_id=None)

    assert bot._chat_consentita(profilo, 111) is True


@patch.dict("os.environ", {}, clear=True)
def test_chat_consentita_senza_allowed_chat_id_ma_con_chat_id_gia_registrato():
    profilo = _profilo_base(chat_id=111)

    assert bot._chat_consentita(profilo, 111) is True
    assert bot._chat_consentita(profilo, 222) is False
