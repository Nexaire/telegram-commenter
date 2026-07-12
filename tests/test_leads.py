import pytest

from app.leads import detect_lead


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Сколько такая машина выйдет под ключ?", "цена"),
        ("Как привезти такую из Кореи?", "доставка"),
        ("Хочу заказать такую машину, куда обратиться?", "покупка"),
        ("Какие сроки доставки автомобиля?", "срок"),
    ],
)
def test_detects_commercial_intent(text, category):
    assert category in detect_lead(text)


def test_ignores_unrelated_comment():
    assert detect_lead("Красивый цвет, особенно на солнце") == []
