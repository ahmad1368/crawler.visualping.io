"""Extractor strategy interface + registry.

Each extractor strategy implements `extract()` for one password-hiding
technique (HTML text, CSS/JS content, HTTP headers/cookies, image
metadata, binary fallback). `ExtractorRegistry` runs every registered
extractor against a fetched response uniformly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models import PasswordMatch


@runtime_checkable
class Extractor(Protocol):
    def extract(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        """Scan `content` for exposed passwords and return any matches found."""
        ...


class ExtractorRegistry:
    def __init__(self) -> None:
        self._extractors: list[Extractor] = []

    def register(self, extractor: Extractor) -> None:
        self._extractors.append(extractor)

    def run_all(self, content: bytes, content_type: str, url: str) -> list[PasswordMatch]:
        matches: list[PasswordMatch] = []
        for extractor in self._extractors:
            matches.extend(extractor.extract(content, content_type, url))
        return matches
