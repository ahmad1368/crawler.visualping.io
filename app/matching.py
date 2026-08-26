"""Password regex matcher + context extractor.

`find_passwords` locates exposed passwords in crawled content and captures
the surrounding text as context. The returned `value`, `context_before`,
and `context_after` are the plaintext secret and its surroundings -- treat
every `RegexMatch` as sensitive from the moment it's created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PASSWORD_PATTERN = re.compile(r"VISUALPING\{[0-9a-f]{16}\}")


@dataclass
class RegexMatch:
    value: str
    context_before: str
    context_after: str
    start: int
    end: int


def find_passwords(content: str, before: int, after: int) -> list[RegexMatch]:
    matches = []
    for match in PASSWORD_PATTERN.finditer(content):
        start, end = match.span()
        matches.append(
            RegexMatch(
                value=match.group(0),
                context_before=content[max(0, start - before) : start],
                context_after=content[end : end + after],
                start=start,
                end=end,
            )
        )
    return matches
