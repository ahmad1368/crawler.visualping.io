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

# The challenge's own worked-example string. It's shaped exactly like a
# real password (so the regex alone can't tell it apart) but is explicitly
# not one of the real finds -- excluded here, once, so every extractor
# gets this for free rather than each hoping the regex is stricter.
KNOWN_EXAMPLE = "VISUALPING{0000deadbeef0000}"


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
        if match.group(0) == KNOWN_EXAMPLE:
            continue
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


def locator_for_offset(text: str, offset: int) -> str:
    """Return a "line:N,col:M" locator string for a character offset."""
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset - last_newline - 1 if last_newline != -1 else offset
    return f"line:{line},col:{column}"
