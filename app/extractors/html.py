"""Extractor for exposed passwords in rendered/raw HTML text nodes and
`<!-- -->` comments.

Matches found in visible text are tagged `SourceType.HTML_TEXT`; matches
found inside comments are tagged `SourceType.HTML_COMMENT`, so downstream
consumers can tell "the operator can see this on the page" apart from
"this secret is only visible in the markup source". Content inside
`<script>`/`<style>` tags is skipped here -- that's covered by the CSS/JS
extractor instead.
"""

from __future__ import annotations

from html.parser import HTMLParser

from app.matching import find_passwords
from app.models import PasswordMatch, SourceType

_SKIPPED_TAGS = {"script", "style"}


class _HtmlChunkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_chunks: list[tuple[str, tuple[int, int]]] = []
        self.comment_chunks: list[tuple[str, tuple[int, int]]] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.text_chunks.append((data, self.getpos()))

    def handle_comment(self, data: str) -> None:
        self.comment_chunks.append((data, self.getpos()))


class HtmlExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("text/html"):
            return []

        collector = _HtmlChunkCollector()
        collector.feed(content.decode("utf-8", errors="replace"))

        matches: list[PasswordMatch] = []
        for data, pos in collector.text_chunks:
            matches.extend(self._matches_for(data, pos, url, SourceType.HTML_TEXT))
        for data, pos in collector.comment_chunks:
            matches.extend(self._matches_for(data, pos, url, SourceType.HTML_COMMENT))
        return matches

    def _matches_for(
        self,
        text: str,
        pos: tuple[int, int],
        url: str,
        source_type: SourceType,
    ) -> list[PasswordMatch]:
        line, column = pos
        return [
            PasswordMatch(
                value=match.value,
                source_type=source_type,
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=f"line:{line},col:{column}",
            )
            for match in find_passwords(
                text, before=self._context_chars, after=self._context_chars
            )
        ]
