"""Port of ingestor/src/pdf/ocr.ts.

FULL_PAGE_OCR route: rasterize the page, run Tesseract, using system
binaries via subprocess rather than embedding a native or WASM OCR
dependency:
  `pdftoppm` (poppler)  -- PDF page -> PNG
  `tesseract`           -- PNG -> text

Deployment note: the runtime container needs both installed
(`apt-get install poppler-utils tesseract-ocr` on Debian/Ubuntu bases) --
tracked as a follow-up alongside the Dockerfile itself, not yet added.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path


async def ocr_page(pdf_bytes: bytes, page_number: int) -> str:
    with tempfile.TemporaryDirectory(prefix="dspace-ocr-") as dir_name:
        dir_path = Path(dir_name)
        pdf_path = dir_path / "page.pdf"
        png_path = dir_path / "page.png"
        pdf_path.write_bytes(pdf_bytes)

        # -f/-l: first/last page (1-indexed, same page both ends -- one
        # page at a time keeps memory bounded on large scanned documents).
        # -r 300: 300dpi, matches the manually-confirmed-working test.
        # -singlefile: without this, pdftoppm names output after the PAGE
        # NUMBER itself (zero-padded to the source doc's total page count),
        # not a predictable "-1" suffix -- -singlefile pins the output to
        # exactly `page.png` regardless of which page number was requested.
        await _run(
            "pdftoppm",
            "-png", "-r", "300", "-singlefile",
            "-f", str(page_number), "-l", str(page_number),
            str(pdf_path), str(dir_path / "page"),
        )  # fmt: skip

        out_prefix = dir_path / "ocr-out"
        await _run("tesseract", str(png_path), str(out_prefix), "--psm", "3")
        text = (out_prefix.with_suffix(".txt")).read_text(encoding="utf-8")
        return text.strip()


async def _run(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"{args[0]} exited with code {process.returncode}: {stderr.decode(errors='replace')[:500]}"
        )


def binaries_available() -> bool:
    """Convenience check for callers/tests that want to skip OCR-dependent
    paths when the required system binaries aren't installed locally."""
    return shutil.which("pdftoppm") is not None and shutil.which("tesseract") is not None
