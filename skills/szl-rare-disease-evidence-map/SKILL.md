---
name: szl-rare-disease-evidence-map
description: Build an offline, source-hash-bound evidence map from synthetic HPO-shaped disease annotations and synthetic ClinVar-shaped SCV/RCV/VCV records. Use for teaching or testing rare-disease evidence provenance, missing links, and submitted-assertion disagreement; not for real patient or variant interpretation.
license: Apache-2.0
---

# Synthetic rare-disease evidence map

Use this skill to rehearse how an evidence map should retain source identity and disagreement before any real research data are considered. The bundled data are invented, not HPO or ClinVar exports. The Python 3.10+ helper uses only the standard library and has no network or credential use.

## Run the exercise

```bash
python -B scripts/run.py assets/example-manifest.json assets/synthetic-hpo.json assets/synthetic-clinvar.json
```

The result is a condition-keyed map, not a ranked list. It retains synthetic HPO-like term IDs interpreted against the declared HPO-shaped source release (the term IDs are not individually versioned), versioned synthetic SCV/RCV/VCV references, each supplied numeric `SYNTH-ASSERT:0001`-style token, divergent-token flags, and missing annotation or variant-record links. The assertion tokens are nonclinical identifiers, not pathogenicity categories. An `EXPLICIT_NEGATIVE_ANNOTATION` is a present, provenance-bearing annotation row; no annotation row for a condition is missing evidence, not a negative annotation. `readiness` is always `HOLD`.

When making a new exercise, read `references/contract.md` and replace the three inputs together. Keep all IDs and labels in the synthetic namespace; the helper rejects actual accession formats, free text, extra fields, duplicate JSON keys, symlinks, and oversized files. The manifest must pin SHA-256 of the exact two source files. The public `map_evidence(manifest_bytes, hpo_bytes, clinvar_bytes)` API parses and hashes the same raw input bytes, and reports `EXACT_INPUT_BYTES` only when each declared source release and digest match. This local byte binding does not authenticate a provider release, justify a biological link, or validate rights. The helper does not fetch, normalize, infer, or fill missing data. Preserve every `HOLD` and divergent token in downstream discussion.

Do not put patient histories, variant calls, identifiers, clinical notes, or real HPO/ClinVar exports in these inputs. Synthetic declarations and token restrictions cannot prove that an operator's inputs were invented. This skill does not diagnose, rank diseases, recommend treatment, classify pathogenicity, or infer that two different submitted tokens are a clinical conflict. For real research, obtain qualified review, lawful source access, release-appropriate HPO/ClinVar documentation, and a separately governed workflow. In particular, HPO says reuse of some OMIM-derived annotations in other software products requires contacting OMIM; no such annotations are bundled here.

The reader verifies the opened file descriptor before reading, caps the bytes read, and rejects observed file replacement or mutation. These are cooperative local-filesystem checks, not an adversarial filesystem sandbox or authenticated history.

Source-format background: [HPO phenotype annotations](https://obophenotype.github.io/human-phenotype-ontology/annotations/phenotype_hpoa/), [ClinVar data model](https://www.ncbi.nlm.nih.gov/clinvar/docs/data_model/), and [ClinVar accession versions](https://www.ncbi.nlm.nih.gov/clinvar/docs/identifiers/). These references describe external formats; this helper deliberately accepts only synthetic analogues. Outside services: none. Credentials: none.
