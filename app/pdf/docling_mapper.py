"""Port of ingestor/src/pdf/docling-mapper.ts.

Builds a real DoclingDocument (docling_types.py) from this pipeline's own
extraction pieces -- the actual "test building Docling-compatible objects
from the native parser" step a schema decision spike calls for.

Headings become genuine parents in the tree (a real hierarchy, not a flat
block list with markdown prefixes like the flat-fulltext path): a level-2
heading's children are every block until the next heading at level <= 2.
Paragraph grouping and column-aware reading order come from
segmentation.py.

content_layer is always "body" here -- reconstruction.py's header/footer/
page-number exclusion already happens upstream (assigned_only_page), so
nothing furniture-tagged ever reaches segmentation in the first place; the
field exists on every node because the schema requires it, not because
this pipeline distinguishes body/furniture itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.pdf.docling_types import (
    DoclingBoundingBox,
    DoclingDocument,
    DoclingPage,
    ProvenanceItem,
    SectionHeaderItem,
    TextItem,
)
from app.pdf.heading import HeadingCandidate
from app.pdf.reconstruction import assigned_only_page
from app.pdf.segmentation import BoundingBoxLike, SegmentationStrategy, segment_native_page
from app.pdf.types import ExtractionRoute, NativePageResult, SourceSpan

BODY_REF = "#/body"
# The real docling-core schema version this was evaluated against (see
# docling_types.py header for sources) -- pinned so a future re-check
# against a newer schema version is an explicit, visible diff.
SCHEMA_VERSION = "1.10.0"


@dataclass
class BuildDoclingDocumentParams:
    native_pages: list[NativePageResult]
    source_spans_by_page: dict[int, list[SourceSpan]]
    headings: list[HeadingCandidate]
    page_routes: dict[int, ExtractionRoute]
    # Only pages that succeeded -- a failed OCR page (see ocr.py/
    # extract.py) simply produces no text block, same as it produces no
    # text in the flat fulltext.
    ocr_text_by_page: dict[int, str]
    document_name: str
    origin_filename: str
    origin_mimetype: str
    origin_binary_hash: str
    # Per-page alternate-assembly override, used by canonical_pipeline
    # .py's completeness-recovery retry -- defaults to AUTO (the normal
    # gutter-detected strategy) for every page not listed here.
    strategy_override_by_page: dict[int, SegmentationStrategy] = field(default_factory=dict)


def _to_docling_bbox(box: BoundingBoxLike) -> DoclingBoundingBox:
    # The native pipeline's coordinates are naturally BOTTOMLEFT-origin (y
    # increases upward) -- emitted as-is, not flipped to TOPLEFT, per
    # docling_types.py's note.
    return {
        "l": box["x"],
        "r": box["x"] + box["width"],
        "b": box["y"],
        "t": box["y"] + box["height"],
        "coord_origin": "BOTTOMLEFT",
    }


def build_docling_document(params: BuildDoclingDocumentParams) -> DoclingDocument:
    texts: list[TextItem | SectionHeaderItem] = []
    body_children: list[dict[str, str]] = []
    pages: dict[str, DoclingPage] = {}
    nodes_by_ref: dict[str, TextItem | SectionHeaderItem] = {}

    # Heading-hierarchy stack: level 0 is the sentinel for "body itself."
    # A new heading pops anything at its own level or deeper, then
    # becomes the new top -- so a level-2 heading closes any open
    # level-2/3 section and opens a fresh one, nested under whatever
    # level-1 section is still open.
    stack: list[dict] = [{"ref": BODY_REF, "level": 0}]

    def add_child(parent_ref: str, child_ref: str) -> None:
        if parent_ref == BODY_REF:
            body_children.append({"$ref": child_ref})
            return
        parent = nodes_by_ref.get(parent_ref)
        if parent is not None:
            parent["children"].append({"$ref": child_ref})

    for page in params.native_pages:
        pages[str(page["pageNumber"])] = {
            "page_no": page["pageNumber"],
            "size": {"width": page["pageWidth"], "height": page["pageHeight"]},
        }

        route = params.page_routes.get(page["pageNumber"])

        if route == "FULL_PAGE_OCR":
            ocr_text = params.ocr_text_by_page.get(page["pageNumber"])
            if not ocr_text:
                continue  # failed OCR page -- no block, honest omission
            ref = f"#/texts/{len(texts)}"
            parent_ref = stack[-1]["ref"]
            prov: list[ProvenanceItem] = [
                {
                    "page_no": page["pageNumber"],
                    "bbox": {
                        "l": 0,
                        "b": 0,
                        "r": page["pageWidth"],
                        "t": page["pageHeight"],
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": (0, len(ocr_text)),
                }
            ]
            item: TextItem = {
                "self_ref": ref,
                "parent": {"$ref": parent_ref},
                "children": [],
                "content_layer": "body",
                "label": "text",
                "orig": ocr_text,
                "text": ocr_text,
                "prov": prov,
            }
            texts.append(item)
            nodes_by_ref[ref] = item
            add_child(parent_ref, ref)
            continue

        # NATIVE_SIMPLE/NATIVE_LAYOUT: real paragraph/heading
        # segmentation, over ASSIGNED spans only (header/footer/page-
        # number already excluded upstream by reconstruction.py).
        spans = params.source_spans_by_page.get(page["pageNumber"], [])
        cleaned_page = assigned_only_page(page, spans)
        strategy = params.strategy_override_by_page.get(page["pageNumber"], "AUTO")
        blocks = segment_native_page(cleaned_page, params.headings, strategy)

        for block in blocks:
            ref = f"#/texts/{len(texts)}"
            prov = [
                {
                    "page_no": block["pageNumber"],
                    "bbox": _to_docling_bbox(block["bbox"]),
                    "charspan": (0, len(block["text"])),
                }
            ]

            if block["kind"] == "heading":
                level = block["level"]
                while len(stack) > 1 and stack[-1]["level"] >= level:
                    stack.pop()
                parent_ref = stack[-1]["ref"]
                heading_item: SectionHeaderItem = {
                    "self_ref": ref,
                    "parent": {"$ref": parent_ref},
                    "children": [],
                    "content_layer": "body",
                    "label": "section_header",
                    "orig": block["orig"],
                    "text": block["text"],
                    "level": level,
                    "prov": prov,
                }
                texts.append(heading_item)
                nodes_by_ref[ref] = heading_item
                add_child(parent_ref, ref)
                stack.append({"ref": ref, "level": level})
            else:
                parent_ref = stack[-1]["ref"]
                paragraph_item: TextItem = {
                    "self_ref": ref,
                    "parent": {"$ref": parent_ref},
                    "children": [],
                    "content_layer": "body",
                    "label": "paragraph",
                    "orig": block["orig"],
                    "text": block["text"],
                    "prov": prov,
                }
                texts.append(paragraph_item)
                nodes_by_ref[ref] = paragraph_item
                add_child(parent_ref, ref)

    return {
        "schema_name": "DoclingDocument",
        "version": SCHEMA_VERSION,
        "name": params.document_name,
        "origin": {
            "mimetype": params.origin_mimetype,
            "binary_hash": params.origin_binary_hash,
            "filename": params.origin_filename,
        },
        "body": {"self_ref": BODY_REF, "children": body_children, "content_layer": "body"},
        "groups": [],  # no list-item detection exists -- always empty, honestly
        "texts": texts,
        "tables": [],  # no real table-cell detection exists -- always empty, honestly
        "pictures": [],  # no image-region detection exists -- always empty, honestly
        "pages": pages,
    }
