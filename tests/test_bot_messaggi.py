import json
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import requests

import bot
import profile_ops
import storage


def _profilo_base(**overrides) -> dict:
    profilo = {**storage.DEFAULT_PROFILE}
    profilo.update(overrides)
    return profilo


def _update(update_id: int = 100, chat_id: int = 42, testo: str | None = "ciao") -> MagicMock:
    update = MagicMock()
    update.update_id = update_id
    update.effective_chat.id = chat_id
    # Il "sta scrivendo…" mandato prima di ogni turno di agente.
    update.effective_chat.send_action = AsyncMock()
    messaggio = MagicMock()
    messaggio.text = testo
    messaggio.caption = None
    messaggio.photo = []
    messaggio.document = None
    messaggio.reply_text = AsyncMock()
    messaggio.reply_document = AsyncMock()
    update.effective_message = messaggio
    return update


# ---------------------------------------------------------------------------
# _rispondi
# ---------------------------------------------------------------------------


@patch("bot.storage.save_profile")
async def test_rispondi_registra_chat_id_salva_e_invia_in_ordine(mock_save):
    update = _update(update_id=7, chat_id=42)
    profilo = _profilo_base(chat_id=None)

    await bot._rispondi(update, profilo, ["primo", "secondo"])

    assert profilo["chat_id"] == 42
    assert profilo["ultimo_update_id"] == 7
    # il profilo salvato deve già contenere l'update_id
    salvato = mock_save.call_args[0][0]
    assert salvato["ultimo_update_id"] == 7
    assert [c.args[0] for c in update.effective_message.reply_text.call_args_list] == ["primo", "secondo"]


# ---------------------------------------------------------------------------
# _diagnostica_errore
# ---------------------------------------------------------------------------


def test_diagnostica_errore_api_llm():
    risposta = MagicMock()
    risposta.headers = {"request-id": "req_123"}
    exc = openai.APIConnectionError(request=MagicMock())
    exc.response = risposta

    messaggio, request_id = bot._diagnostica_errore(exc)

    assert "ragionare" in messaggio
    assert request_id == "req_123"


def test_diagnostica_errore_persistenza():
    messaggio, request_id = bot._diagnostica_errore(storage.StorageError("boom"))
    assert "profilo" in messaggio
    assert request_id is None

    messaggio, _ = bot._diagnostica_errore(requests.exceptions.ConnectionError())
    assert "profilo" in messaggio

    messaggio, _ = bot._diagnostica_errore(json.JSONDecodeError("bad", "doc", 0))
    assert "profilo" in messaggio


def test_diagnostica_errore_generico():
    messaggio, request_id = bot._diagnostica_errore(RuntimeError("boh"))
    assert "storto" in messaggio
    assert request_id is None


# ---------------------------------------------------------------------------
# _con_gestione_errori
# ---------------------------------------------------------------------------


async def test_con_gestione_errori_non_propaga_e_risponde_col_messaggio_giusto():
    async def handler_che_esplode(update, context):
        raise storage.StorageError("boom")

    update = _update()

    await bot._con_gestione_errori(handler_che_esplode)(update, None)

    testo = update.effective_message.reply_text.call_args.args[0]
    assert "profilo" in testo


# ---------------------------------------------------------------------------
# dedup per update_id
# ---------------------------------------------------------------------------


def test_update_gia_processato():
    profilo = _profilo_base(ultimo_update_id=None)
    assert bot._update_gia_processato(profilo, _update(update_id=5)) is False

    profilo = _profilo_base(ultimo_update_id=5)
    assert bot._update_gia_processato(profilo, _update(update_id=5)) is True
    assert bot._update_gia_processato(profilo, _update(update_id=4)) is True
    assert bot._update_gia_processato(profilo, _update(update_id=6)) is False


def test_update_gia_processato_regge_un_ultimo_update_id_corrotto():
    profilo = profile_ops.normalizza_profilo({"ultimo_update_id": "100"})
    assert bot._update_gia_processato(profilo, _update(update_id=5)) is False


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_messaggio_ignora_un_update_gia_processato(mock_load, mock_save, mock_handle):
    mock_load.return_value = _profilo_base(chat_id=42, ultimo_update_id=100, onboarding_completato=True)
    update = _update(update_id=100)

    await bot.messaggio(update, None)

    mock_handle.assert_not_called()
    mock_save.assert_not_called()
    update.effective_message.reply_text.assert_not_called()


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_messaggio_processa_un_update_nuovo(mock_load, mock_save, mock_handle):
    profilo = _profilo_base(chat_id=42, ultimo_update_id=99, onboarding_completato=True)
    mock_load.return_value = profilo
    mock_handle.return_value = (profilo, ["ok"])
    update = _update(update_id=100, testo="pasta")

    await bot.messaggio(update, None)

    mock_handle.assert_called_once_with(profilo, "pasta", None, None)
    update.effective_message.reply_text.assert_called_once_with("ok")


@patch.dict("os.environ", {"ALLOWED_CHAT_ID": "42"}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.load_profile")
async def test_messaggio_ignora_una_chat_non_consentita(mock_load, mock_handle):
    mock_load.return_value = _profilo_base(chat_id=42)
    update = _update(chat_id=999)

    await bot.messaggio(update, None)

    mock_handle.assert_not_called()
    update.effective_message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# foto inviata come documento
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_messaggio_con_documento_png_propaga_il_media_type(mock_load, mock_save, mock_handle):
    profilo = _profilo_base(chat_id=42, onboarding_completato=True)
    mock_load.return_value = profilo
    mock_handle.return_value = (profilo, ["ok"])

    update = _update(testo=None)
    file_scaricato = MagicMock()
    file_scaricato.download_as_bytearray = AsyncMock(return_value=bytearray(b"png-bytes"))
    documento = MagicMock()
    documento.mime_type = "image/png"
    documento.get_file = AsyncMock(return_value=file_scaricato)
    update.effective_message.document = documento

    await bot.messaggio(update, None)

    mock_handle.assert_called_once_with(profilo, None, b"png-bytes", "image/png")


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_messaggio_con_foto_usa_jpeg(mock_load, mock_save, mock_handle):
    profilo = _profilo_base(chat_id=42, onboarding_completato=True)
    mock_load.return_value = profilo
    mock_handle.return_value = (profilo, ["ok"])

    update = _update(testo=None)
    file_scaricato = MagicMock()
    file_scaricato.download_as_bytearray = AsyncMock(return_value=bytearray(b"jpeg-bytes"))
    foto = MagicMock()
    foto.get_file = AsyncMock(return_value=file_scaricato)
    update.effective_message.photo = [foto]

    await bot.messaggio(update, None)

    mock_handle.assert_called_once_with(profilo, None, b"jpeg-bytes", "image/jpeg")


# ---------------------------------------------------------------------------
# catch-all
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_non_gestito_risponde_se_la_chat_e_consentita(mock_load, mock_save):
    mock_load.return_value = _profilo_base(chat_id=42)
    update = _update()

    await bot.non_gestito(update, None)

    assert "Non so gestire" in update.effective_message.reply_text.call_args.args[0]


@patch.dict("os.environ", {"ALLOWED_CHAT_ID": "42"}, clear=True)
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_non_gestito_tace_se_la_chat_non_e_consentita(mock_load, mock_save):
    mock_load.return_value = _profilo_base(chat_id=42)
    update = _update(chat_id=999)

    await bot.non_gestito(update, None)

    update.effective_message.reply_text.assert_not_called()
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# comandi di servizio
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_sblocca_chat_command_azzera_il_chat_id_senza_riassegnarlo(mock_load, mock_save):
    mock_load.return_value = _profilo_base(chat_id=42)
    update = _update(update_id=8, chat_id=42)

    await bot.sblocca_chat_command(update, None)

    salvato = mock_save.call_args[0][0]
    assert salvato["chat_id"] is None
    assert salvato["ultimo_update_id"] == 8
    assert "sbloccata" in update.effective_message.reply_text.call_args.args[0].lower()


@patch.dict("os.environ", {}, clear=True)
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_export_command_invia_il_profilo_come_documento(mock_load, mock_save):
    mock_load.return_value = _profilo_base(chat_id=42, allergie_intolleranze=["glutine"])
    update = _update(update_id=9, chat_id=42)

    await bot.export_command(update, None)

    kwargs = update.effective_message.reply_document.call_args.kwargs
    assert kwargs["filename"] == "profilo-mensa.json"
    contenuto = json.loads(kwargs["document"].getvalue().decode("utf-8"))
    assert contenuto["allergie_intolleranze"] == ["glutine"]
    assert mock_save.call_args[0][0]["ultimo_update_id"] == 9


@patch.dict("os.environ", {}, clear=True)
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_export_command_rivendica_la_chat_come_rispondi(mock_load, mock_save):
    mock_load.return_value = _profilo_base(chat_id=None)
    update = _update(update_id=9, chat_id=42)

    await bot.export_command(update, None)

    salvato = mock_save.call_args[0][0]
    assert salvato["chat_id"] == 42
    assert salvato["ultimo_update_id"] == 9


# ---------------------------------------------------------------------------
# comandi conversazionali: semi per l'agente, non percorsi a parte
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_start_passa_dall_agente_con_il_proprio_seme(mock_load, mock_save, mock_processa):
    profilo = _profilo_base(chat_id=42)
    mock_load.return_value = profilo
    mock_processa.return_value = (profilo, ["ciao!"])

    await bot.start(_update(update_id=1), None)

    mock_processa.assert_called_once_with(profilo, bot.handlers.SEME_START, None, None)


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_feedback_passa_dall_agente_con_il_proprio_seme(mock_load, mock_save, mock_processa):
    profilo = _profilo_base(chat_id=42, onboarding_completato=True)
    mock_load.return_value = profilo
    mock_processa.return_value = (profilo, ["com'è andata?"])

    await bot.feedback_command(_update(update_id=2), None)

    mock_processa.assert_called_once_with(profilo, bot.handlers.SEME_FEEDBACK, None, None)


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_prima_di_un_turno_di_agente_parte_il_sta_scrivendo(mock_load, mock_save, mock_processa):
    profilo = _profilo_base(chat_id=42)
    mock_load.return_value = profilo
    mock_processa.return_value = (profilo, ["ok"])
    update = _update()

    await bot.messaggio(update, None)

    update.effective_chat.send_action.assert_awaited_once()


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.handle_preferenze_command")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_preferenze_resta_deterministico(mock_load, mock_save, mock_handle):
    """È una lettura di dati locali: non deve costare una chiamata al modello."""
    profilo = _profilo_base(chat_id=42)
    mock_load.return_value = profilo
    mock_handle.return_value = (profilo, ["riepilogo"])
    update = _update(update_id=3)

    await bot.preferenze_command(update, None)

    mock_handle.assert_called_once_with(profilo)
    update.effective_chat.send_action.assert_not_awaited()


@patch.dict("os.environ", {}, clear=True)
@patch("bot.handlers.processa_messaggio")
@patch("bot.storage.save_profile")
@patch("bot.storage.load_profile")
async def test_la_didascalia_di_una_foto_arriva_all_agente(mock_load, mock_save, mock_processa):
    """Con un allegato il testo del messaggio è la caption: dice spesso cosa
    farne, quindi non va persa."""
    profilo = _profilo_base(chat_id=42, onboarding_completato=True)
    mock_load.return_value = profilo
    mock_processa.return_value = (profilo, ["ok"])

    update = _update(testo=None)
    update.effective_message.caption = "questo è quello che ho mangiato"
    file_scaricato = MagicMock()
    file_scaricato.download_as_bytearray = AsyncMock(return_value=bytearray(b"jpeg"))
    foto = MagicMock()
    foto.get_file = AsyncMock(return_value=file_scaricato)
    update.effective_message.photo = [foto]

    await bot.messaggio(update, None)

    mock_processa.assert_called_once_with(
        profilo, "questo è quello che ho mangiato", b"jpeg", "image/jpeg"
    )
