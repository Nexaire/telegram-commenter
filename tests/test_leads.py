import pytest

from app.leads import CoverRequestDetector, normalize


@pytest.fixture
def detector():
    return CoverRequestDetector(
        keywords=["кавер-групп", "кавер групп", "живая музыка"],
        intent_phrases=[
            "ищем",
            "нужна",
            "требуется",
            "на свадьбу",
            "на корпоратив",
        ],
        exclude_phrases=["мы кавер-группа", "наша кавер-группа"],
        require_intent=True,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Ищем кавер-группу на корпоратив 20 декабря",
        "На свадьбу нужна кавер группа. Москва.",
        "Требуется ЖИВАЯ МУЗЫКА на вечернее мероприятие",
        "Ищем кавер—группу на праздник",
    ],
)
def test_detects_cover_band_requests(detector, text):
    assert detector.detect(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Мы кавер-группа из Москвы, выступаем на праздниках",
        "Послушали отличную кавер-группу",
        "Ищем фотографа на корпоратив",
        "Наша кавер группа выпустила новый ролик",
    ],
)
def test_ignores_non_requests(detector, text):
    assert detector.detect(text) is None


def test_can_match_keyword_without_intent():
    detector = CoverRequestDetector(keywords=["кавер-группа"], require_intent=False)
    assert detector.detect("Подскажите контакты: кавер-группа") is not None


def test_normalizes_case_whitespace_yo_and_dash():
    assert normalize(" КАВЕР—ГРУППА\nЁлка ") == "кавер-группа елка"
