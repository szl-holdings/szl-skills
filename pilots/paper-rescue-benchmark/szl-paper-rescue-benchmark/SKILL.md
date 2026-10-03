---
name: szl-paper-rescue-benchmark
description: Compares source-linked table-cell and figure-caption records from old scientific PDFs with a separate adjudicated reference, counting abstentions and rejecting malformed locators. Use for a rights-cleared, local PDF extraction pilot before claiming evidence quality.
license: Apache-2.0
disable-model-invocation: true
---

# Paper rescue benchmark pilot

Use this only for an authorized local scientific PDF. It scores *declared* extracted records against a separate, independently prepared reference. It does not extract, OCR, inspect page pixels, validate the reference's independence, verify rights, establish scientific truth, or qualify clinical use. Keep the PDF and any page crops local; do not send them to a hosted service by default.

1. Record the document's local-processing permission and rights basis. Exclude patient data. Obtain a PDF and extraction records from a separately chosen, pinned pipeline; keep its version and OCR engine in the record. For Docling JSON, preselect candidate IDs and source references before extraction review, then run the optional [offline adapter](scripts/from_docling.py) using [its selection contract](references/docling-adapter.md). Its `records.json` and separate provenance JSON are candidate inputs only. Prepare reference annotations separately, without showing annotators the candidate records. Do not create gold from the candidate output.
2. Create the two JSON files according to [the contract](references/contract.md). Every selected query needs a reference item. Mark uncertain candidate items `UNRESOLVED` with a reason, not a guessed page, cell, or value. Preserve full cell text, row/column headers, unit, and bounding box; for figures, record only a linked caption, not an inferred plotted value.
3. Run `python -B scripts/run.py --pdf paper.pdf --records records.json --gold adjudicated.json --output report.json`. Review every mismatch and abstention against the actual image. The report is an unsigned, local comparison to a self-declared reference, not a finding of visual or scientific truth.

Exit 0 means a valid comparison with no located claim that contradicts the declared reference; it can still contain missing items and abstentions. Exit 1 means at least one located claim mismatched the reference. Exit 2 means invalid input or I/O. `--output` never overwrites an existing report. The script uses Python 3.9+ standard library, no credentials, no network, and no model download. Optional upstream OCR/model installation is outside this skill and requires separate resource, rights, and privacy review.

This source pilot is not in the marketplace or Claude Science installer and has no published benchmark. The existing `szl-paper-evidence-audit` is the separate claim-to-Docling-JSON locator; this pilot tests extraction records against independent annotations. A source hash binds bytes, not source-to-extraction lineage or visual correctness.
