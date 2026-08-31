"""Extractor for exposed passwords in raw HTML source.

Scans the raw markup directly with a regex rather than DOM-extracted
visible text, so tag attribute values (e.g. a `data-*` attribute) and any
other markup content are covered by the same pass -- a DOM-text-only scan
never sees attribute values at all, since they aren't part of any text
node.

Matches inside `<!-- -->` are tagged `SourceType.HTML_COMMENT`; everything
else (visible text, attributes, inline `<script>`/`<style>` content) is
tagged `SourceType.HTML_TEXT`, so downstream consumers can still tell "the
operator can see this rendered" apart from "this secret is only visible in
the markup source." Only the comment split is special-cased -- without it
a comment's password would be double-reported as both types.
"""

from __future__ import annotations

import re

from app.matching import find_passwords, locator_for_offset
from app.models import PasswordMatch, SourceType

_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)


class HtmlExtractor:
    def __init__(self, context_chars: int = 80) -> None:
        self._context_chars = context_chars

    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        if not content_type.startswith("text/html"):
            return []

        html_text = content.decode("utf-8", errors="replace")
        comment_spans = [m.span() for m in _COMMENT_PATTERN.finditer(html_text)]

        return [
            PasswordMatch(
                value=match.value,
                source_type=(
                    SourceType.HTML_COMMENT
                    if self._in_comment(match.start, comment_spans)
                    else SourceType.HTML_TEXT
                ),
                source_url=url,
                context_before=match.context_before,
                context_after=match.context_after,
                locator=locator_for_offset(html_text, match.start),
            )
            for match in find_passwords(
                html_text, before=self._context_chars, after=self._context_chars
            )
        ]

    @staticmethod
    def _in_comment(offset: int, spans: list[tuple[int, int]]) -> bool:
        return any(start <= offset < end for start, end in spans)
