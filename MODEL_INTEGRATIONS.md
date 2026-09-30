# Optional SZL models for scientific workflows

The pack works without an inference provider. Model assistance should add a measurable
capability to the scientist's task. These are integration candidates, not benchmarks of the
new skills or a claim that any model is qualified for an arbitrary scientific domain.

## Research-memory retrieval: MiniEmbed Nano

Source candidate: [SZLHOLDINGS/MiniEmbed-Nano](https://huggingface.co/SZLHOLDINGS/MiniEmbed-Nano/tree/01e82f36ff722528233f76daf899c18f8cb5aaa2).
The observed tree contains mini_embed.npz and training/provenance/benchmark records.
Inspect the recorded dimensions and domain, source loader and training/evaluation evidence.
Use non-executable array loading with pickle disabled when the selected format supports it.
Do not assume it embeds arbitrary scientific text well. Evaluate recall on a small scientist-
labeled set of questions and relevant documents, against lexical search, before using it
to retrieve nodes for the research anatomy. Keep citations and artifact ids through ranking.
If its supported vocabulary or dimensions do not suit the task, record that mismatch.

## Research navigation: BrainNavigator R2

Source candidate: [SZLHOLDINGS/brain-navigator-r2](https://huggingface.co/SZLHOLDINGS/brain-navigator-r2/tree/7fe3872fcb78ec8f5cc2f0c7464538b50b34e4b2).
The observed tree contains weight/adapter files and evaluation, merge and training receipts.
Inspect base-to-adapter binding and the actual model card before inference. A sensible pilot
is proposing which already indexed artifacts to inspect next, while requiring existing
source ids for every suggestion. Score retrieval relevance and unsupported-source rate.
An output never sets a graph claim to PROVEN or establishes that a paper supports it.

## Evidence triage: five-seed adapter study

Source candidate: [SZLHOLDINGS/szl-triage-qwen3.5-0.8b-lora-study5](https://huggingface.co/SZLHOLDINGS/szl-triage-qwen3.5-0.8b-lora-study5/tree/eb79a26a2934d5eaa667984720feacdcb90dcc28).
The observed tree contains per-seed adapters, frozen split files and saved prediction/metric
records. Inspect those records before running new inference; retain the declared task and
label set. The bundled model helper scores binary probabilities only: do not coerce generated
triage labels or multiclass outputs into fake binary probabilities. Use the appropriate
evaluator for that task and compare the actual per-seed results with the baseline. Treat
source findings, model judgments and human adjudication as separate fields.

## Shared evidence boundaries

Metadata observations were made on 2026-09-29; files were listed, not loaded or authenticated.
Weights, receipt files and reachable pages do not establish scientific quality, private-data
permission or held-out evaluation integrity. All three observed cards declare Apache-2.0;
check upstream/base-model conditions separately. No model weights or research datasets are
included in this repository or its ZIPs. Lambda remains Conjecture 1 (OPEN).

External services: optional public metadata/artifact reads contact huggingface.co or
github.com and send selected ids/revisions. Private assets and remote inference require the
user's existing credential and an explicitly chosen transmission. The bundled helpers make
no service calls and require no keys.
