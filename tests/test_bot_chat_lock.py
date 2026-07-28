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


@patch.dict("os.environ", {"ALLOWED_CHAT_ID": "12345"}, clear=True)
def test_chat_autorizzata_usa_il_nome_vecchio_della_variabile_come_fallback():
    """Chi aveva ALLOWED_CHAT_ID (pre-refactor mono-utente) non deve ritrovarsi
    il bot aperto a chiunque per una rinomina di variabile mai propagata."""
    assert bot._chat_autorizzata(12345) is True
    assert bot._chat_autorizzata(99999) is False


@patch.dict("os.environ", {"ALLOWED_CHAT_IDS": "12345", "ALLOWED_CHAT_ID": "99999"}, clear=True)
def test_chat_autorizzata_preferisce_il_nome_nuovo_della_variabile():
    assert bot._chat_autorizzata(12345) is True
    assert bot._chat_autorizzata(99999) is False
