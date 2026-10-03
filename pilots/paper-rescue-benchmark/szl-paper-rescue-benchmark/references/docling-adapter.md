# Offline Docling candidate adapter contract

`scripts/from_docling.py` creates only candidate `records.json` and a separate
`provenance.json`; it never opens `gold.json`. Preselect IDs and Docling JSON
references before reviewing extraction. The UTF-8 selection JSON (at most
1,000,000 bytes) has exactly `schema`, `pdf_sha256`,
`document_json_sha256`, `declared_pages`, `rights`, `pipeline`, and `items`.
The schema is `szl.paper-rescue.docling-selection.v1`. `rights` and `pipeline`
have the exact shapes in the [scorer contract](contract.md) and are checked
**before** opening the PDF or Docling JSON. Both input hashes must match their
exact bytes. The Docling JSON limit is 32 MiB; its `pages` mapping must provide
finite, positive page sizes for exactly the declared 1-based pages.

Each selected item has exactly `id`, `kind`, and `ref`. IDs and item-count bounds
are the same as `records.json`; duplicate IDs or source references fail. A
`cell` reference is `#/tables/N/data/table_cells/M`; a `caption` reference is
`#/pictures/N`. No text, header, unit, page, or box is supplied by the
selection. A located cell requires its own cell box, complete text, explicit
string `row_header`, `column_header`, and `unit`, plus one valid table page
region. The adapter does not infer these fields or borrow a whole-table box.
A located caption requires one linked `#/texts/N` item with its own box on the
same page as a single valid picture region. Unknown, incomplete, ambiguous,
or unsupported selected items become `UNRESOLVED` with a reason. Docling
bottom-left coordinates are converted using that page's dimensions to the
benchmark's normalized top-left box; malformed or out-of-page regions do not
become located claims. Every region must explicitly declare `coord_origin` as
`TOPLEFT` or `BOTTOMLEFT`; no origin is inferred. If a cell also has its own
`prov` box, each normalized coordinate must agree with the cell box within an
absolute tolerance of `1e-9`, with zero relative tolerance, or the candidate
abstains. This numerical conversion tolerance accommodates equivalent origin
representations; it does not establish agreement with page pixels or visual
correctness.

```text
python -I -B scripts/from_docling.py --pdf paper.pdf --document-json paper.docling.json --selection selection.json --output records.json --provenance-output provenance.json
```

Both output paths must be absent; each is created exclusively and never
overwritten. A failed second write can leave the first file without a matching
provenance file; quarantine that partial output rather than scoring it. The
provenance JSON binds the exact PDF, Docling JSON, selection JSON, and emitted
record bytes by SHA-256. It explicitly does **not** verify rights, that Docling
actually extracted from this PDF, page pixels, scientific truth, or clinical
use. The current model-free NASA extraction has zero table objects and no
linked picture captions, so all eight preselected cells and two captions must
remain `UNRESOLVED`; no numeric accuracy score follows from that abstention.
