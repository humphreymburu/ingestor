"""Port of ingestor/src/pdf/continuation.ts.

Cross-page paragraph continuation, deterministic-rule version (no model).
Without this, a paragraph split across page N/N+1 produces two separate
blobs of text joined by the same "\\n\\n" used for a genuine section break
-- indistinguishable in the output from an unrelated new paragraph
starting on the next page.

Rule: the last line of page N has no terminal punctuation AND the first
line of page N+1 starts with a lowercase letter -> real continuation
signal, join with a single space instead of a page break. Applied
uniformly across all page-text boundaries regardless of route (native or
OCR) -- purely textual, no page-geometry dependency.
"""

from __future__ import annotations

import re

NO_TERMINAL_PUNCTUATION_RE = re.compile("[.!?:;)\\]\"'”’]$")


def _ends_paragraph(line: str) -> bool:
    trimmed = line.strip()
    if not trimmed:
        return False
    return bool(NO_TERMINAL_PUNCTUATION_RE.search(trimmed))


def _starts_like_continuation(line: str) -> bool:
    trimmed = line.strip()
    if not trimmed:
        return False
    first = trimmed[0]
    # A genuine lowercase LETTER, not a digit/symbol/bullet marker -- real
    # sentence continuations start with a word, not "3." or "-". A
    # character is a lowercase letter iff lowercasing it is a no-op AND it
    # has a distinct uppercase form (excludes digits/symbols, which are
    # unchanged by both).
    return first == first.lower() and first != first.upper()


# Exposed so a future canonical pipeline stage can record the SAME
# decision as an explicit CONTINUATION relationship between two blocks,
# rather than only using it to silently join strings.
def is_continuation_boundary(last_line: str, first_line: str) -> bool:
    return not _ends_paragraph(last_line) and _starts_like_continuation(first_line)


def join_pages_with_continuation(page_texts: list[str]) -> str:
    non_empty = [t for t in page_texts if t.strip()]
    if not non_empty:
        return ""

    result = non_empty[0]
    for next_text in non_empty[1:]:
        prev_lines = result.split("\n")
        next_lines = next_text.split("\n")
        last_line = prev_lines[-1] if prev_lines else ""
        first_line = next_lines[0] if next_lines else ""

        if is_continuation_boundary(last_line, first_line):
            prev_lines[-1] = f"{last_line.strip()} {first_line.strip()}"
            result = "\n".join(prev_lines + next_lines[1:])
        else:
            result = f"{result}\n\n{next_text}"
    return result
