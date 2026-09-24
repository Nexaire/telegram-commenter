from app.workflow_bot import TRANSITIONS, keyboard_for, split_report


def button_texts(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_new_keyboard():
    markup = keyboard_for(17, "new", "https://t.me/source/1")
    assert button_texts(markup) == [["Открыть"], ["В работу", "Не заявка"]]
    assert markup.inline_keyboard[1][0].callback_data == "lead:work:17"


def test_in_progress_and_terminal_keyboards():
    in_progress = keyboard_for(17, "in_progress", "https://t.me/source/1")
    irrelevant = keyboard_for(17, "irrelevant", "https://t.me/source/1")
    assert button_texts(in_progress) == [["Открыть"], ["Договорились", "Отказ"]]
    assert button_texts(irrelevant) == [["Открыть"]]


def test_status_transitions_are_limited():
    assert TRANSITIONS[("new", "work")] == "in_progress"
    assert TRANSITIONS[("new", "irrelevant")] == "irrelevant"
    assert TRANSITIONS[("in_progress", "agreed")] == "agreed"
    assert TRANSITIONS[("in_progress", "rejected")] == "rejected"
    assert ("irrelevant", "work") not in TRANSITIONS


def test_report_is_split_without_losing_leads():
    lines = [f"{index}. " + "x" * 1000 for index in range(1, 10)]
    chunks = split_report("header", lines)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)
    combined = "\n".join(chunks)
    assert all(line in combined for line in lines)


def test_empty_report_has_explanation():
    assert "нет" in split_report("header", [])[0].lower()
