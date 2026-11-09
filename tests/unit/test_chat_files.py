"""Копии вложений чата в своём хранилище (chat/files): в тестах диск (S3 выключен)."""

from tg_studio.config import settings
from tg_studio.modules.chat import files as chat_files


def test_save_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    rel = chat_files.save_message_file(b"hello-bytes", "image/jpeg")
    assert rel.startswith("chat/messages/")
    assert rel.endswith(".jpg")
    assert chat_files.read_message_file(rel) == b"hello-bytes"


def test_read_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    assert chat_files.read_message_file("chat/messages/nope.png") is None


def test_ext_for_known_mimes():
    assert chat_files._ext_for("image/jpeg") == "jpg"
    assert chat_files._ext_for("image/png") == "png"
    assert chat_files._ext_for("audio/ogg") == "ogg"
    assert chat_files._ext_for("audio/mpeg") == "mp3"
    assert chat_files._ext_for("video/mp4") == "mp4"
    assert chat_files._ext_for("application/octet-stream") == "bin"
    assert chat_files._ext_for("image/webp") == "bin"  # неизвестный mime → bin
