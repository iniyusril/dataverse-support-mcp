"""Convert Dataverse HTML to plain text for the model, and model text back to safe HTML."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "head", "title"}
_BLOCK = {
    "p", "div", "br", "li", "tr", "table", "blockquote", "ul", "ol",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None, max_chars: int | None = None) -> str:
    """Strip markup (scripts and styles included) and tidy whitespace."""
    if not value:
        return ""
    parser = _Extractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # malformed markup: crude fallback
        text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[... truncated]"
    return text


def text_to_html(text: str, signature: str = "") -> str:
    """Turn plain text into minimal HTML. Everything is escaped, so the model
    cannot inject markup, links or scripts into an outgoing email."""

    def paragraph(block: str) -> str:
        escaped = html.escape(block.strip(), quote=False).replace("\n", "<br>")
        return f"<p>{escaped}</p>"

    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if signature.strip():
        blocks.append(signature.strip())
    return "<div>" + "".join(paragraph(b) for b in blocks) + "</div>"
