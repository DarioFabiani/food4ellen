# tests/test_bot_chat_lock.py
from unittest.mock import patch

import bot


@patch.dict("os.environ", {"ALLOWED_CHAT_IDS": "12345,67890"}, clear=False)
def test_chat_autorizzata_con_lista_configurata():
    assert bot._chat_autorizzata(12345) is True
    assert bot._chat_autorizzata(67890) is True
    assert bot._chat_autorizzata(99999) is False


@patch.dict("os.environ", {"ALLOWED_CHAT_IDS": " 12345 , 67890 "}, clear=False)
def test_chat_autorizzata_ignora_spazi_nella_lista():
    assert bot._chat_autorizzata(12345) is True
    assert bot._chat_autorizzata(67890) is True


@patch.dict("os.environ", {}, clear=True)
def test_chat_autorizzata_senza_lista_configurata_e_sempre_vera():
    assert bot._chat_autorizzata(111) is True
    assert bot._chat_autorizzata(222) is True
