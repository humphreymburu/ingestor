"""Port of ingestor/src/pdf/routing-features.ts.

Cheap, no-layout-model routing features, computed directly from the native
text-layer geometry (see native.py's module docstring for the pdfjs-dist ->
pdfplumber swap). Ported from the measurement-based audit's
docs/pdf-audit/profiler.py (Python/PyMuPDF), which is the source of truth
for every threshold used downstream in router.py.

Two measurements profiler.py had that this port deliberately does NOT
reproduce, and why:
  - `tables_per_sampled_page` used PyMuPDF's find_tables(), a real
    table-detection algorithm with no equivalent here. table_proxy_score
    below is a cheaper text-geometry-only heuristic (row/column alignment +
    numeric density) -- NOT a like-for-like replacement, see the comment on
    _compute_table_proxy_score. Treat its threshold as unvalidated until
    measured against real output.
  - non-embedded-font detection needs font-descriptor access this pass
    doesn't use yet -- omitted, not faked.
"""

from __future__ import annotations

import re

from app.pdf.types import NativePageResult, PageRoutingFeatures, Script, ScriptDistribution

# Same set as profiler.py FUNCTION_WORDS -- English only, deliberately
# conservative. A low ratio on genuinely non-English text is NOT evidence
# of garbled extraction; cross-check against script_distribution first.
FUNCTION_WORDS = {
    "the", "of", "and", "to", "in", "a", "is", "that", "for", "on", "with",
    "as", "by", "at", "from", "this", "be", "are", "was", "were", "or",
    "an", "it", "which", "has", "have", "not", "its", "their", "will",
}  # fmt: skip

# Same ranges as profiler.py SCRIPT_RANGES (CJK -> HAN, matching the
# architecture doc's naming).
SCRIPT_RANGES: list[tuple[str, list[tuple[int, int]]]] = [
    ("LATIN", [(0x0041, 0x024F), (0x0000, 0x007F)]),
    ("CYRILLIC", [(0x0400, 0x04FF)]),
    ("ARABIC", [(0x0600, 0x06FF), (0x0750, 0x077F)]),
    ("HAN", [(0x4E00, 0x9FFF), (0x3040, 0x30FF)]),
]

_REPLACEMENT_CHAR_RE = re.compile("�")
_NON_WORD_APOSTROPHE_RE = re.compile(r"[^a-zA-Z0-9_']")  # JS's \w is ASCII-only, unaffected by /u


def _detect_char_script(code_point: int) -> str | None:
    for script, ranges in SCRIPT_RANGES:
        for lo, hi in ranges:
            if lo <= code_point <= hi:
                return script
    return None


def _script_distribution(text: str) -> tuple[ScriptDistribution, Script]:
    distribution: ScriptDistribution = {
        "LATIN": 0, "ARABIC": 0, "CYRILLIC": 0, "HAN": 0, "DEVANAGARI": 0, "OTHER": 0
    }  # fmt: skip
    for ch in text:
        script = _detect_char_script(ord(ch))
        if script:
            distribution[script] += 1  # type: ignore[literal-required]

    dominant: Script = "UNKNOWN"
    max_count = 0
    for script, count in distribution.items():
        if count > max_count:
            max_count = count
            dominant = script  # type: ignore[assignment]
    return distribution, dominant


# Vertical-gutter column detection -- direct port of profiler.py's
# word_column_estimate: histogram span x-coverage into 40 bins, find
# interior runs of near-empty bins (>=2 bins wide) flanked by dense bins.
def _column_count_estimate(page: NativePageResult) -> int:
    if not page["spans"] or page["pageWidth"] <= 0:
        return 1

    n_bins = 40
    bin_width = page["pageWidth"] / n_bins
    coverage = [0] * n_bins

    for span in page["spans"]:
        x0 = span["x"]
        x1 = span["x"] + span["width"]
        b0 = max(0, min(n_bins - 1, int(x0 // bin_width)))
        b1 = max(0, min(n_bins - 1, int(x1 // bin_width)))
        for b in range(b0, b1 + 1):
            coverage[b] += 1

    max_cov = max(coverage)
    if max_cov == 0:
        return 1
    threshold = max_cov * 0.05
    empty = [c <= threshold for c in coverage]

    margin = 3
    gutters = 0
    i = margin
    while i < n_bins - margin:
        if empty[i] and not empty[i - 1]:
            j = i
            while j < n_bins - margin and empty[j]:
                j += 1
            if j - i >= 2:
                gutters += 1
            i = j
        else:
            i += 1
    return gutters + 1


# Cheap table-likelihood proxy (NOT equivalent to profiler.py's PyMuPDF
# find_tables()). Rows are formed by clustering spans on y (tolerance in
# points); a row "looks tabular" if it has several short, numeric-heavy
# spans landing in stable x-bands that recur across multiple rows. Returns
# 0-1: share of rows that look tabular among rows with enough spans to
# judge.
def _compute_table_proxy_score(page: NativePageResult) -> float:
    if len(page["spans"]) < 6:
        return 0

    y_tolerance = 3
    rows: list[dict] = []
    for span in sorted(page["spans"], key=lambda s: s["y"]):
        row = next((r for r in rows if abs(r["y"] - span["y"]) <= y_tolerance), None)
        if row:
            row["spans"].append(span)
        else:
            rows.append({"y": span["y"], "spans": [span]})

    judgeable = [r for r in rows if len(r["spans"]) >= 3]
    if len(judgeable) < 3:
        return 0

    # A row "looks tabular" if most of its spans are short (<=15 chars,
    # i.e. cell-like rather than prose) and numeric-token-heavy.
    tabular_rows = 0
    for row in judgeable:
        short_spans = [s for s in row["spans"] if 0 < len(s["text"].strip()) <= 15]
        numeric_spans = [s for s in short_spans if re.search(r"\d", s["text"])]
        if len(short_spans) >= len(row["spans"]) * 0.6 and len(numeric_spans) >= len(short_spans) * 0.4:
            tabular_rows += 1
    return round((tabular_rows / len(judgeable)) * 100) / 100


def compute_page_features(page: NativePageResult) -> PageRoutingFeatures:
    text = " ".join(s["text"] for s in page["spans"])
    tokens = re.findall(r"\S+", text)

    # JS's `[...text]` iterates by codepoint (not UTF-16 code unit) --
    # Python string iteration is codepoint-based natively, matching that.
    # `\p{L}` (any Unicode letter) is approximated with str.isalpha(),
    # which corresponds to the same Unicode letter categories.
    alpha_chars = sum(1 for c in text if c.isalpha())
    alpha_ratio = round((alpha_chars / len(text)) * 1000) / 1000 if text else 0

    replacement_char_count = len(_REPLACEMENT_CHAR_RE.findall(text))

    long_tokens = [t for t in tokens if len(t) > 18]
    long_token_ratio = round((len(long_tokens) / len(tokens)) * 1000) / 1000 if tokens else 0

    lower_tokens = [_NON_WORD_APOSTROPHE_RE.sub("", t.lower()) for t in tokens]
    fw_hits = sum(1 for t in lower_tokens if t in FUNCTION_WORDS)
    function_word_ratio = round((fw_hits / len(lower_tokens)) * 1000) / 1000 if lower_tokens else 0

    distribution, dominant = _script_distribution(text)

    return {
        "charCount": len(text),
        "alphaRatio": alpha_ratio,
        "functionWordRatio": function_word_ratio,
        "replacementCharCount": replacement_char_count,
        "longTokenRatio": long_token_ratio,
        "columnCountEstimate": _column_count_estimate(page),
        "tableProxyScore": _compute_table_proxy_score(page),
        "dominantScript": dominant,
        "scriptDistribution": distribution,
    }
