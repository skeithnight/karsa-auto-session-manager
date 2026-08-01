"""app/bot/utils/format.py — Composable Telegram HTML Formatters.

Auto-escapes plain strings. HTML marker class prevents double-escaping.
Usage: fmt(bold("Title"), "\\n", code("/cmd"), " — description")

Ported from karsa-claude-trading src/utils/format.py.
Import path updated: app.bot.utils.format (was src.utils.format).
"""

from html import escape
from typing import Union

TextLike = Union[str, "HTML", None]


class HTML(str):
    """Marker class for HTML-safe strings. Prevents double-escaping."""

    pass


def _safe(text: TextLike) -> str:
    """Escape plain text, pass through already-safe HTML."""
    if text is None:
        return ""
    return text if isinstance(text, HTML) else escape(str(text))


# ── Basic Formatters ──────────────────────────────────────────────
def bold(t: TextLike) -> HTML:
    return HTML(f"<b>{_safe(t)}</b>")


def italic(t: TextLike) -> HTML:
    return HTML(f"<i>{_safe(t)}</i>")


def underline(t: TextLike) -> HTML:
    return HTML(f"<u>{_safe(t)}</u>")


def strike(t: TextLike) -> HTML:
    return HTML(f"<s>{_safe(t)}</s>")


def code(t: TextLike) -> HTML:
    return HTML(f"<code>{_safe(t)}</code>")


def pre(t: TextLike, lang: str = None) -> HTML:
    """Code block. Optional language for syntax hint."""
    content = _safe(t)
    if lang:
        return HTML(
            f'<pre><code class="language-{escape(lang)}">{content}</code></pre>'
        )
    return HTML(f"<pre>{content}</pre>")


def link(t: TextLike, url: str) -> HTML:
    return HTML(f'<a href="{escape(url)}">{_safe(t)}</a>')


# ── Composers ─────────────────────────────────────────────────────
def fmt(*parts: TextLike, sep: str = "") -> HTML:
    """Join parts with optional separator. Auto-escapes plain text."""
    return HTML(sep.join(_safe(p) for p in parts if p is not None))


def join(items: list[TextLike], sep: str = "\n") -> HTML:
    """Join list of items with separator."""
    return fmt(*items, sep=sep)
