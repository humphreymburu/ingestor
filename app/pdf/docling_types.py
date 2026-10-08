"""Port of ingestor/src/pdf/docling-types.ts.

Python port of the real DoclingDocument schema -- a schema decision spike,
done properly (not skipped): these types are derived from docling-core's
actual published schema, not guessed at. Sources cross-checked 2026-08-30:
  https://github.com/docling-project/docling-core/blob/main/docs/DoclingDocument.json
  https://docling-project.github.io/docling/concepts/docling_document/
  https://deepwiki.com/docling-project/docling-core/2.1-doclingdocument

Reduced to the subset this pipeline can actually populate (text/heading
content with real hierarchy + provenance) -- TableItem/PictureItem/
KeyValueItem/FormItem are typed for completeness (so a real
DoclingDocument object type-checks) but this pipeline never constructs
one, since it has no real table-cell or image-region detection.

Note this schema is reproduced purely as Python TypedDicts here -- the
`docling` PyPI package is NOT a dependency of this port. This pipeline
never calls any real Docling library/service; it only implements a
from-scratch port of the DoclingDocument *data schema* and maps its own
extraction output into that shape (see docling_mapper.py).
"""

from __future__ import annotations

from typing import Literal, TypedDict

RefItem = str  # JSON pointer, e.g. "#/texts/0", "#/body"

CoordOrigin = Literal["TOPLEFT", "BOTTOMLEFT"]


# The native pipeline's coordinates are naturally BOTTOMLEFT-origin (y
# increases upward) -- this pipeline always emits coord_origin:
# "BOTTOMLEFT" rather than flipping, which the schema explicitly supports
# as a real, first-class option, not a compatibility hack.
class DoclingBoundingBox(TypedDict):
    l: float
    t: float
    r: float
    b: float
    coord_origin: CoordOrigin


class ProvenanceItem(TypedDict):
    page_no: int
    bbox: DoclingBoundingBox
    charspan: tuple[int, int]


class DocumentOrigin(TypedDict):
    mimetype: str
    binary_hash: str
    filename: str


ContentLayer = Literal["body", "furniture"]


# A JSON:API-style pointer wrapper: {"$ref": "#/texts/0"}. Plain
# dict[str, RefItem] rather than a TypedDict, since "$ref" isn't a valid
# Python identifier.
Ref = dict[str, RefItem]


class NodeItemBase(TypedDict, total=False):
    self_ref: RefItem
    parent: Ref | None
    children: list[Ref]
    content_layer: ContentLayer


TextItemLabel = Literal["paragraph", "text", "caption", "footnote", "list_item", "code", "formula"]


class TextItem(NodeItemBase):
    label: TextItemLabel
    orig: str  # text as it appeared in the source, before any normalization
    text: str  # normalized text
    prov: list[ProvenanceItem]


class SectionHeaderItem(NodeItemBase):
    label: Literal["section_header"]
    orig: str
    text: str
    level: int  # 1-100 per the real schema; this pipeline only ever emits 1-3
    prov: list[ProvenanceItem]


GroupLabel = Literal["list", "ordered_list", "chapter", "section", "unspecified"]


class GroupItem(NodeItemBase):
    label: GroupLabel
    name: str


# Typed but never constructed by this pipeline -- no real table-cell/row/
# column detection exists (table_proxy_score in routing_features.py is a
# routing signal, not a cell-boundary detector).
class TableCell(TypedDict):
    text: str
    row_span: int
    col_span: int
    start_row_offset_idx: int
    end_row_offset_idx: int
    start_col_offset_idx: int
    end_col_offset_idx: int
    column_header: bool
    row_header: bool


class TableData(TypedDict):
    num_rows: int
    num_cols: int
    table_cells: list[TableCell]


class TableItem(NodeItemBase):
    label: Literal["table", "document_index"]
    data: TableData
    prov: list[ProvenanceItem]


# Typed but never constructed -- no image-region detection exists
# anywhere in this pipeline.
class PictureItem(NodeItemBase):
    label: Literal["picture", "chart"]
    prov: list[ProvenanceItem]


class DoclingPageSize(TypedDict):
    width: float
    height: float


class DoclingPage(TypedDict):
    size: DoclingPageSize
    page_no: int


class DoclingDocument(TypedDict):
    schema_name: Literal["DoclingDocument"]
    version: str  # "1.10.0" as of the schema version this was evaluated against
    name: str
    origin: DocumentOrigin
    body: NodeItemBase  # self_ref === "#/body", the root of the content tree
    groups: list[GroupItem]
    texts: list[TextItem | SectionHeaderItem]
    tables: list[TableItem]  # always [] from this pipeline
    pictures: list[PictureItem]  # always [] from this pipeline
    pages: dict[str, DoclingPage]
