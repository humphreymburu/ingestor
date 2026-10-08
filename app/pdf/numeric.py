"""Port of ingestor/src/pdf/numeric.ts.

Lightweight numeric-token counting -- the extraction/measurement half
only, no independent second-reader verification (that needs a real second
extraction path to compare against, deliberately deferred). This exists so
a future decision about whether real numeric verification is worth
building rests on measured numeric density in this actual corpus, not a
guess.
"""

from __future__ import annotations

import re

from app.pdf.types import NumericTokenCounts

# Priority order matters -- each token is classified into exactly ONE
# category (first match wins), so totals reflect distinct tokens rather
# than overlapping regex hits (e.g. "45%" is a percentage, not also
# counted as a decimal).
PERCENT_RE = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?%$")
CURRENCY_RE = re.compile(
    r"^(?:[$€£¥]|USD|EUR|GBP)\s?\d[\d,]*(\.\d+)?$|^\d[\d,]*(\.\d+)?\s?(?:USD|EUR|GBP)$",
    re.IGNORECASE,
)
# Common numeric date shapes: 12/31/2025, 31-12-2025, 2025.12.31 -- not an
# exhaustive date grammar, just enough to separate "this is a date" from
# "this is a bare quantity."
DATE_RE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}$")
DECIMAL_RE = re.compile(r"^[+-]?\d[\d,]*\.\d+$")
INTEGER_RE = re.compile(r"^[+-]?\d[\d,]*$")

_STRIP_PUNCTUATION_RE = re.compile(r"^[([{]+|[)\]},;:.]+$")


def count_numeric_tokens(text: str) -> NumericTokenCounts:
    counts: NumericTokenCounts = {
        "percentages": 0, "currencies": 0, "dates": 0, "decimals": 0, "integers": 0, "total": 0
    }  # fmt: skip
    tokens = re.findall(r"\S+", text)

    for raw in tokens:
        # Strip surrounding punctuation a sentence would add (opening
        # brackets, trailing comma/semicolon/colon/closing-bracket/
        # sentence-ending period) without touching internal separators a
        # real token needs -- "12/31/2025." (sentence-final) must still
        # match the date pattern; "3.14" must not lose its decimal point.
        token = _STRIP_PUNCTUATION_RE.sub("", raw)
        if not re.search(r"\d", token):
            continue

        if PERCENT_RE.match(token):
            counts["percentages"] += 1
        elif CURRENCY_RE.match(token):
            counts["currencies"] += 1
        elif DATE_RE.match(token):
            counts["dates"] += 1
        elif DECIMAL_RE.match(token):
            counts["decimals"] += 1
        elif INTEGER_RE.match(token):
            counts["integers"] += 1
        else:
            continue  # contains a digit but isn't one of these shapes (e.g. "COVID-19", "Q3")

        counts["total"] += 1

    return counts
