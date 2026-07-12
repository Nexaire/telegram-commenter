from types import SimpleNamespace

import pytest

from app.llm import CommentGenerator


def generator():
    settings = SimpleNamespace(
        gigachat_credentials="x",
        gigachat_scope="GIGACHAT_API_PERS",
        gigachat_model="GigaChat-2",
        gigachat_base_url="https://gigachat.devices.sberbank.ru/api/v1",
        gigachat_verify_ssl_certs=False,
        gigachat_ca_bundle_file=None,
        blacklist_topics=[],
        brand_names=["Nexaire"],
    )
    return CommentGenerator(settings)


def test_rejects_links():
    with pytest.raises(ValueError): generator().validate("Details at https://example.com")


def test_rejects_brand():
    with pytest.raises(ValueError): generator().validate("Try Nexaire today")


def test_accepts_clean_comment():
    assert generator().validate("A concrete useful observation.") == "A concrete useful observation."


def test_rejects_direct_advertising():
    with pytest.raises(ValueError): generator().validate("Подпишитесь на наш канал")
