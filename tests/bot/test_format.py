"""Tests for HTML formatting helpers."""

from __future__ import annotations


from app.bot.utils.format import bold, italic, code, pre, fmt, join, HTML


class TestHTMLFragments:
    def test_bold_wraps_in_b_tags(self):
        assert str(bold("hello")) == "<b>hello</b>"

    def test_italic_wraps_in_i_tags(self):
        assert str(italic("hello")) == "<i>hello</i>"

    def test_code_wraps_in_code_tags(self):
        assert str(code("hello")) == "<code>hello</code>"

    def test_pre_wraps_in_pre_tags(self):
        assert str(pre("hello")) == "<pre>hello</pre>"

    def test_html_str_returns_self(self):
        h = HTML("test")
        assert str(h) == "test"

    def test_html_add_concatenates(self):
        result = bold("a") + bold("b")
        assert str(result) == "<b>a</b><b>b</b>"

    def test_html_radd(self):
        result = "prefix" + bold("text")
        assert str(result) == "prefix<b>text</b>"

    def test_fmt_composes_multiple_parts(self):
        result = fmt(bold("a"), " ", code("b"))
        assert str(result) == "<b>a</b> <code>b</code>"

    def test_fmt_skips_none(self):
        result = fmt(bold("a"), None, code("b"))
        assert str(result) == "<b>a</b><code>b</code>"

    def test_join_with_newline(self):
        result = join([bold("a"), bold("b")])
        assert str(result) == "<b>a</b>\n<b>b</b>"

    def test_join_custom_separator(self):
        result = join(["a", "b", "c"], sep=", ")
        assert str(result) == "a, b, c"

    def test_empty_fmt(self):
        result = fmt()
        assert str(result) == ""
