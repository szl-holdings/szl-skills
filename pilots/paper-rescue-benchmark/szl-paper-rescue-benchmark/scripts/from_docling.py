#!/usr/bin/env python3
"""Convert selected local Docling items to conservative paper-rescue records.

This adapter reads neither reference annotations nor page pixels. A Docling
document is an unverified candidate extraction, not visual ground truth.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kernel import (RECORD_SCHEMA, digest, exact_keys, item_id, require,
                    validate_item, validate_pipeline, validate_records,
                    validate_rights)  # noqa: E402
from scripts.run import (duplicate_pairs, pdf_digest, reject_constant,
                         write_exclusive)  # noqa: E402


SELECTION_SCHEMA = "szl.paper-rescue.docling-selection.v1"
PROVENANCE_SCHEMA = "szl.paper-rescue.docling-provenance.v1"
DOCUMENT_LIMIT = 32 * 1024 * 1024
SELECTION_LIMIT = 1_000_000
TABLE_REF = re.compile(r"#/tables/(0|[1-9][0-9]*)/data/table_cells/(0|[1-9][0-9]*)\Z")
PICTURE_REF = re.compile(r"#/pictures/(0|[1-9][0-9]*)\Z")
TEXT_REF = re.compile(r"#/texts/(0|[1-9][0-9]*)\Z")


def finite_float(token):
    if len(token) > 32:
        raise ValueError("NUMBER_LIMIT")
    result = float(token)
    if not math.isfinite(result):
        raise ValueError("NONFINITE_NUMBER")
    return result


def bounded_integer(token):
    # Docling's origin.binary_hash is a 64-bit unsigned decimal (20 digits).
    if len(token) > 32:
        raise ValueError("INTEGER_LIMIT")
    return int(token)


def read_json(path, limit):
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON_SIZE_LIMIT")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=duplicate_pairs,
                           parse_int=bounded_integer, parse_float=finite_float,
                           parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("MALFORMED_JSON") from exc
    return value, hashlib.sha256(raw).hexdigest()


def validate_selection(selection):
    exact_keys(selection, ("schema", "pdf_sha256", "document_json_sha256",
                           "declared_pages", "rights", "pipeline", "items"))
    require(selection["schema"] == SELECTION_SCHEMA, "BAD_SELECTION_SCHEMA")
    digest(selection["pdf_sha256"])
    digest(selection["document_json_sha256"])
    pages = selection["declared_pages"]
    require(type(pages) is int and 1 <= pages <= 10000, "BAD_DECLARED_PAGES")
    validate_rights(selection["rights"])
    validate_pipeline(selection["pipeline"])
    items = selection["items"]
    require(type(items) is list and 1 <= len(items) <= 1000, "BAD_ITEM_COUNT")
    seen_ids, seen_refs = set(), set()
    for item in items:
        exact_keys(item, ("id", "kind", "ref"))
        item_id(item["id"])
        require(item["id"] not in seen_ids, "DUPLICATE_ITEM_ID")
        seen_ids.add(item["id"])
        require(type(item["ref"]) is str and item["ref"] not in seen_refs,
                "DUPLICATE_SOURCE_REF")
        seen_refs.add(item["ref"])
        pattern = TABLE_REF if item["kind"] == "cell" else (
            PICTURE_REF if item["kind"] == "caption" else None)
        require(pattern is not None and pattern.fullmatch(item["ref"]) is not None,
                "BAD_SOURCE_REF")


def page_sizes(document, declared_pages):
    require(type(document) is dict and type(document.get("pages")) is dict,
            "BAD_DOCLING_PAGES")
    pages = document["pages"]
    require(set(pages) == {str(n) for n in range(1, declared_pages + 1)},
            "PAGE_DECLARATION_MISMATCH")
    result = {}
    for number in range(1, declared_pages + 1):
        page = pages[str(number)]
        require(type(page) is dict and type(page.get("size")) is dict,
                "BAD_DOCLING_PAGES")
        if "page_no" in page:
            require(type(page["page_no"]) is int and page["page_no"] == number,
                    "BAD_DOCLING_PAGES")
        width, height = (page["size"].get(name) for name in ("width", "height"))
        require(all(type(x) in (int, float) and math.isfinite(x) and 0 < x <= 100000
                    for x in (width, height)), "BAD_DOCLING_PAGES")
        result[number] = width, height
    return result


def single_page(item, sizes):
    provenance = item.get("prov") if type(item) is dict else None
    if type(provenance) is not list or len(provenance) != 1:
        return None
    entry = provenance[0]
    page = entry.get("page_no") if type(entry) is dict else None
    if type(page) is not int or page not in sizes:
        return None
    return page if normalized_box(entry.get("bbox"), sizes[page]) is not None else None


def normalized_box(box, size):
    if type(box) is not dict:
        return None
    coordinates = [box.get(name) for name in ("l", "t", "r", "b")]
    if not all(type(value) in (int, float) and math.isfinite(value)
               for value in coordinates):
        return None
    left, top, right, bottom = coordinates
    width, height = size
    origin = box.get("coord_origin")
    if not (0 <= left < right <= width and 0 <= top <= height and
            0 <= bottom <= height):
        return None
    if origin == "TOPLEFT" and top < bottom:
        result = [left / width, top / height, right / width, bottom / height]
    elif origin == "BOTTOMLEFT" and top > bottom:
        result = [left / width, (height - top) / height,
                  right / width, (height - bottom) / height]
    else:
        return None
    return result if result[0] < result[2] and result[1] < result[3] else None


def unresolved(item, reason):
    return {"id": item["id"], "kind": item["kind"], "status": "UNRESOLVED",
            "reason": reason}


def selected_cell(document, item, sizes):
    table_number, cell_number = map(int, TABLE_REF.fullmatch(item["ref"]).groups())
    tables = document.get("tables")
    if type(tables) is not list or table_number >= len(tables):
        return unresolved(item, "TABLE_NOT_FOUND")
    table = tables[table_number]
    page = single_page(table, sizes)
    data = table.get("data") if type(table) is dict else None
    cells = data.get("table_cells") if type(data) is dict else None
    if type(cells) is not list or cell_number >= len(cells):
        return unresolved(item, "CELL_NOT_FOUND")
    cell = cells[cell_number]
    if type(cell) is not dict or page is None:
        return unresolved(item, "CELL_PROVENANCE_UNRESOLVED")
    if "prov" in cell and single_page(cell, sizes) != page:
        return unresolved(item, "CELL_PAGE_AMBIGUOUS")
    box = normalized_box(cell.get("bbox"), sizes[page])
    if box is None:
        return unresolved(item, "CELL_REGION_MISSING")
    if "prov" in cell:
        provenance_box = normalized_box(cell["prov"][0]["bbox"], sizes[page])
        if not all(math.isclose(actual, declared, rel_tol=0, abs_tol=1e-9)
                   for actual, declared in zip(box, provenance_box)):
            return unresolved(item, "CELL_REGION_AMBIGUOUS")
    candidate = {"id": item["id"], "kind": "cell", "status": "LOCATED",
                 "page": page, "bbox": box, "text": cell.get("text"),
                 "row_header": cell.get("row_header"),
                 "column_header": cell.get("column_header"), "unit": cell.get("unit")}
    try:
        validate_item(candidate, len(sizes))
    except ValueError:
        return unresolved(item, "CELL_FIELDS_INCOMPLETE")
    return candidate


def selected_caption(document, item, sizes):
    picture_number = int(PICTURE_REF.fullmatch(item["ref"]).group(1))
    pictures = document.get("pictures")
    if type(pictures) is not list or picture_number >= len(pictures):
        return unresolved(item, "PICTURE_NOT_FOUND")
    picture = pictures[picture_number]
    picture_page = single_page(picture, sizes)
    captions = picture.get("captions") if type(picture) is dict else None
    if picture_page is None or type(captions) is not list or len(captions) != 1:
        return unresolved(item, "CAPTION_LINK_MISSING_OR_AMBIGUOUS")
    link = captions[0]
    if (type(link) is dict and "$ref" in link and "cref" in link and
            link["$ref"] != link["cref"]):
        return unresolved(item, "CAPTION_LINK_INVALID")
    reference = link.get("$ref", link.get("cref")) if type(link) is dict else None
    match = TEXT_REF.fullmatch(reference) if type(reference) is str else None
    texts = document.get("texts")
    if match is None or type(texts) is not list or int(match.group(1)) >= len(texts):
        return unresolved(item, "CAPTION_LINK_INVALID")
    caption = texts[int(match.group(1))]
    if single_page(caption, sizes) != picture_page:
        return unresolved(item, "CAPTION_PICTURE_PAGE_MISMATCH")
    box = normalized_box(caption["prov"][0].get("bbox"), sizes[picture_page])
    if box is None:
        return unresolved(item, "CAPTION_REGION_MISSING")
    candidate = {"id": item["id"], "kind": "caption", "status": "LOCATED",
                 "page": picture_page, "bbox": box, "text": caption.get("text")}
    try:
        validate_item(candidate, len(sizes))
    except ValueError:
        return unresolved(item, "CAPTION_TEXT_INVALID")
    return candidate


def build_records(selection, document, pdf_sha256):
    validate_selection(selection)
    require(selection["pdf_sha256"] == pdf_sha256, "PDF_HASH_MISMATCH")
    sizes = page_sizes(document, selection["declared_pages"])
    items = [(selected_cell(document, item, sizes) if item["kind"] == "cell" else
              selected_caption(document, item, sizes)) for item in selection["items"]]
    records = {"schema": RECORD_SCHEMA, "pdf_sha256": pdf_sha256,
               "declared_pages": selection["declared_pages"],
               "rights": selection["rights"], "pipeline": selection["pipeline"],
               "items": items}
    validate_records(records, pdf_sha256)
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--document-json", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provenance-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        selection, selection_hash = read_json(args.selection, SELECTION_LIMIT)
        validate_selection(selection)  # Rights/privacy checks precede PDF and document reads.
        require(args.output.resolve() != args.provenance_output.resolve(),
                "OUTPUT_PATH_COLLISION")
        require(not args.output.exists() and not args.provenance_output.exists(),
                "OUTPUT_ALREADY_EXISTS")
        pdf_hash = pdf_digest(args.pdf)
        require(pdf_hash == selection["pdf_sha256"], "PDF_HASH_MISMATCH")
        document, document_hash = read_json(args.document_json, DOCUMENT_LIMIT)
        require(document_hash == selection["document_json_sha256"],
                "DOCUMENT_HASH_MISMATCH")
        records = build_records(selection, document, pdf_hash)
        rendered = json.dumps(records, sort_keys=True, indent=2, allow_nan=False) + "\n"
        provenance = {"schema": PROVENANCE_SCHEMA, "pdf_sha256": pdf_hash,
                      "document_json_sha256": document_hash,
                      "selection_json_sha256": selection_hash,
                      "records_json_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                      "item_count": len(records["items"]),
                      "unresolved_count": sum(i["status"] == "UNRESOLVED"
                                              for i in records["items"]),
                      "rights_verified": False, "docling_pdf_lineage_verified": False,
                      "visual_truth_verified": False, "clinical_use_qualified": False}
        receipt = json.dumps(provenance, sort_keys=True, indent=2) + "\n"
        write_exclusive(args.output, rendered)
        write_exclusive(args.provenance_output, receipt)
    except ValueError as exc:
        sys.stderr.write(f"INVALID_INPUT: {exc}\n")
        return 2
    except OSError:
        sys.stderr.write("INPUT_OR_OUTPUT_IO: no overwrite; inspect partial output if any\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
