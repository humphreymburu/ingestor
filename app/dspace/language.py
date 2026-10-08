"""Port of ingestor/src/dspace/language.ts.

Precedence per the spec, but only as many tiers as are actually groundable
right now:
  1. bitstream language metadata (dc.language on the bitstream) -- not seen
     populated on any bitstream checked live; dc.description is what's
     actually used for this on this instance (tier 2).
  2. bitstream description metadata -- confirmed live and reliable: a
     DSpace bitstream's dc.description carries a full language name
     ("English", "Arabic", "Chinese", ...), not a langcode.
  3. known UNEP filename suffix convention -- confirmed live on two real
     6-language Publications-community items with DIFFERENT suffix
     conventions for the same languages (EGR2025 uses ZH/ES, FB027 uses
     CH/SP for Chinese/Spanish) -- both variants must resolve.
  4. item dc.language, only if the item is single-language.
  5. language detection from extracted text -- needs Tika, not built.
  6. "und" fallback.
"""

from __future__ import annotations

import re

from app.types import DspaceBitstream, DspaceItem

LANGUAGE_NAME_TO_CODE: dict[str, str] = {
    "english": "en",
    "arabic": "ar",
    "chinese": "zh",
    "french": "fr",
    "russian": "ru",
    "spanish": "es",
    "portuguese": "pt",
    "german": "de",
}

# The prefix before the language suffix varies per publication (e.g. "ES"
# for "Executive Summary" in EGR2025_ES*.pdf), so this only checks the
# trailing 2-letter code, not a fixed full-filename pattern.
FILENAME_SUFFIX_TO_CODE: dict[str, str] = {
    "EN": "en",
    "AR": "ar",
    "ZH": "zh",
    "CH": "zh",  # FB027 uses CH for Chinese; EGR2025 uses ZH for the same language
    "FR": "fr",
    "RU": "ru",
    "ES": "es",
    "SP": "es",  # FB027/AMR/CHRP use SP for Spanish; EGR2025 uses ES for the same language
}

_FILENAME_SUFFIX_RE = re.compile(r"([A-Z]{2})$")


def _metadata_language_name(bitstream: DspaceBitstream, field: str) -> str | None:
    values = bitstream.get("metadata", {}).get(field)
    raw = (values[0]["value"].strip() if values and values[0].get("value") else None)
    if not raw:
        return None
    return LANGUAGE_NAME_TO_CODE.get(raw.lower())


def _language_from_filename_suffix(filename: str) -> str | None:
    base = re.sub(r"\.[^.]+$", "", filename)  # strip extension
    match = _FILENAME_SUFFIX_RE.search(base)
    if not match:
        return None
    return FILENAME_SUFFIX_TO_CODE.get(match.group(1))


def _item_language_if_unambiguous(item: DspaceItem) -> str | None:
    values = item.get("metadata", {}).get("dc.language")
    if not values or len(values) != 1:
        return None
    raw = values[0].get("value", "").strip() if values[0].get("value") else None
    if not raw:
        return None
    return LANGUAGE_NAME_TO_CODE.get(raw.lower())


def detect_bitstream_language(item: DspaceItem, bitstream: DspaceBitstream) -> dict[str, str | None]:
    from_metadata_language = _metadata_language_name(bitstream, "dc.language")
    if from_metadata_language:
        return {"language": from_metadata_language, "source": "bitstream_language"}

    from_description = _metadata_language_name(bitstream, "dc.description")
    if from_description:
        return {"language": from_description, "source": "bitstream_description"}

    from_filename = _language_from_filename_suffix(bitstream["name"])
    if from_filename:
        return {"language": from_filename, "source": "filename_convention"}

    from_item = _item_language_if_unambiguous(item)
    if from_item:
        return {"language": from_item, "source": "item_language"}

    # Tier 5 (text-based language detection) needs extracted fulltext,
    # which needs Tika -- not built yet. Falls through to "und" until that
    # exists.
    return {"language": "und", "source": None}
