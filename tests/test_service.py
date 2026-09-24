from types import SimpleNamespace

from app.service import message_url, notification_text


def test_public_message_url():
    entity = SimpleNamespace(username="music_chat", id=123, megagroup=True)
    assert message_url(entity, 42) == "https://t.me/music_chat/42"


def test_private_supergroup_message_url():
    entity = SimpleNamespace(
        username=None, id=123456, megagroup=True, broadcast=False
    )
    assert message_url(entity, 42) == "https://t.me/c/123456/42"


def test_basic_private_chat_has_no_link():
    entity = SimpleNamespace(
        username=None, id=123456, megagroup=False, broadcast=False
    )
    assert message_url(entity, 42) is None


def test_notification_escapes_html_and_fits_telegram_limit():
    body = notification_text(
        "A < B", "<script>" + "x" * 5000, "https://t.me/test/1?a=1&b=2"
    )
    assert "&lt;script&gt;" in body
    assert "A &lt; B" in body
    assert len(body) <= 4096
