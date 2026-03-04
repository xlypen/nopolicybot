from utils.text_formatting import capitalize_sentences, reply_text_to_html, strip_leading_name


def test_capitalize_sentences():
    assert capitalize_sentences("hello. world? ok!") == "hello. World? Ok!"


def test_strip_leading_name():
    assert strip_leading_name("Иван, привет", "Иван") == "привет"
    assert strip_leading_name("user: text", "Иван", "user") == "text"


def test_reply_text_to_html_with_code_block():
    src = "text\n```python\nprint(1)\n```\nend"
    html = reply_text_to_html(src)
    assert "<pre><code>" in html
    assert "print(1)" in html
