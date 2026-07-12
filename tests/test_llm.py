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


@pytest.mark.parametrize("label", ["Комментарий 1: ", "Вариант №2 — ", "Comment 2. "])
def test_removes_comment_label(label):
    assert generator().validate(label + "Конкретная полезная мысль.") == "Конкретная полезная мысль."


def test_rejects_direct_advertising():
    with pytest.raises(ValueError): generator().validate("Подпишитесь на наш канал")


def test_parses_json_in_markdown_fence():
    data = generator().parse_json('```json\n{"variants": ["one", "two"]}\n```')
    assert data == {"variants": ["one", "two"]}


def test_rejects_non_object_json():
    with pytest.raises(ValueError):
        generator().parse_json('["one", "two"]')
