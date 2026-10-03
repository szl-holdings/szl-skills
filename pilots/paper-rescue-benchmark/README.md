# Paper rescue benchmark source pilot

This is an **unreleased, synthetic-only evaluation harness**, not a Claude Science registration, OCR system, model benchmark, or clinical tool. It adds a rights-gated comparison of table-cell/figure-caption extraction records to a separate adjudicated reference. It deliberately does not change the core skill inventory, marketplace, installer, or published release.

Run from the repository root:

```text
python -I -B -m unittest discover -s tests -p test_paper_rescue_benchmark.py -v
python -B tools/selfcheck.py
```

The tests generate a tiny operator-created `%PDF-` fixture and separate JSON files in a temporary directory. The PDF bytes are only a hash-binding fixture; they contain no table or caption pixels. The scorer's synthetic matches establish schema, tamper refusal, denominator accounting, and CLI behavior only. They do not measure old-PDF extraction accuracy or annotator independence.

The next research step is **not** promotion: record two independent visual annotations for the already selected NASA public-use pilot, compare a separately pinned local OCR/table pipeline to its model-free native baseline, and only then freeze a rights-cleared held-out set. The baseline emitted 559 text items and zero table objects across 13 pages, but it has no human gold or accuracy score. See the candidate [skill](szl-paper-rescue-benchmark/SKILL.md) and [contract](szl-paper-rescue-benchmark/references/contract.md).
