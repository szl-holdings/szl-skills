# SZL frontier research — adversarial harness evaluation, skills, defensive agents, and scientific claim boundaries

**Scope:** primary-repository/read-only research + one new offline evidence-audit
contract. Collected 2026-11-16/17. The six supplied screenshots are *leads*,
not immutable source documents or independent verification. Star counts and
GitHub pushes below were queried directly from GitHub on Nov 17 and change
with time. **No external code was installed or executed.**

## 1. Screenshot-by-screenshot audit

| Screenshot | What is visible | Status and implications |
| --- | --- | --- |
| “The End of Harness Engineering” one-page poster | A frozen large model, a 4B editor, eight suggested edits per round, held-out scoring, and a harness file changed after wins; it says the human writes the scoring set. | **Poster assertion, not an independently verified research result.** The 0.31→0.64/41-round story circulates in social summaries without a cited primary paper/benchmark. Repeatedly using even hidden test feedback to choose the winner makes that set a development set: indirect test-set reuse is optimization. |
| AI offensive tooling social post | Cairn, CyberStrike variants, PentAGI, PentestGPT, Shannon, Strix and others. | **Defensive threat-intelligence reference only.** Some frameworks can autonomously exploit targets; none should be installed, launched against targets, or given SZL credentials by this research. Study state machines and evidence models, not offensive playbooks. |
| “rx top” Blackwell terminal | Claims ten RTX PRO 6000 Blackwell nodes, each 96 GB, 960 GB fleet memory, GPU utilization and ~$24.90/hour. | NVIDIA confirms the product's **96 GB per GPU**; the screenshot is not a verified allocation, invoice, or actual SZL fleet. The depicted 960 GB is aggregate and does not create a pooled 960 GB per-job memory space. Budget and admission require provider receipts, workload rights, token/energy accounting, and exact owner approval. |
| “Mass as Nested Fields” poster | Displays `M0 = ħc/Lu ≈ 0.828 GeV` at `Lu ≈ 2.38×10⁻¹⁶ m`, a nested-field mass conjecture, four pictorial particle categories. | The scale is an **energy** `E ≈ ħc/Lu ≈ 0.83 GeV` with `ħc ≈ 0.1973 GeV·fm` and `Lu ≈ 0.238 fm`. Expressing it as a mass requires `E/c²` (or natural units `c = 1`). That arithmetic does not derive electron, neutron or proton masses or prove a physical theory. Requires explicit Lagrangian, gauge symmetries, boundary conditions, parameter-count penalty, predictions, experimental residuals and independent peer review before citation as physics. |
| Multi-Agent Fleet UI | 66 agent labels, “8 running”, token and dollar figures, tree of tasks and subagents. | This is an unverified screenshot of a UI, not an SZL runtime attestation. Adapt only bounded graph observability: span/agent/role/budget, identity of source and tool result, completion proof, queue and cost labels. Agent count and displayed tokens alone are not capability measures. |
| Viral “AI configs on GitHub” post | Matt Pocock, Addy Osmani, Cloudflare audit, Obsidian and Google Elephant/Goldfish repositories with claimed stars. | These are genuine software-repository families, but social star counts are volatile and none is a turnkey production skill. Audit version, license, external tool execution, privilege boundaries and independent acceptance before any adoption. |

## 2. Verified primary sources and design patterns

### Agent evaluation and adaptive search

- [Google DeepMind AlphaEvolve technical report (arXiv:2506.13131)](https://arxiv.org/abs/2506.13131): model-proposed program revisions with externally executable evaluators. Promising **pattern**: frozen, reproducible graders and independently verified candidate programs, not a proof that every agent loop improves.
- [Wang et al., *Rethinking the Evaluation of Harness Evolution for Agents* (arXiv:2607.12227)](https://arxiv.org/abs/2607.12227): test-time scaling baselines, matched compute and feedback, and unseen-task transfer are essential. The authors find that harness evolution does not consistently beat simpler approaches and has limited held-out generalization.
- [Wang et al., *Beneath the Surface* (arXiv:2611.07624)](https://arxiv.org/abs/2611.07624): evolution can gain tasks while losing previous coverage and increasing deployment cost; preserve prior coverage and measure budget.
- [Dwork et al., *The reusable holdout* (Science, 2015)](https://pubmed.ncbi.nlm.nih.gov/26250683/): repeat, adaptive reuse of a purported holdout requires special statistical controls. The offline checker below does **not** implement a reusable-holdout algorithm.

**SZL rule:** (i) immutable pre-registered grader with positive and negative controls,
(ii) a development set that candidate search may repeatedly query,
(iii) **a different sealed, frozen, never-feedback-driven final set**, used once,
(iv) match total model calls and actual time/cost against sampling/refinement
baselines, (v) preserve previous working tasks, (vi) report source/data hashes
and explicit authority with no automatic promotion.

### Skills and software engineering — GitHub API readback (2026-11-17 UTC)

| Source | Public status at readback | Useful pattern; trade-off |
| --- | --- | --- |
| [mattpocock/skills](https://github.com/mattpocock/skills) | ~326,047 stars; MIT; unarchived | Small interface-focused workflows, issue-to-spec/ticket graph, fresh code review and retros. Adapt interfaces and invariant checks, not wholesale prompts. |
| [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | ~118,387 stars; MIT; unarchived | Explicit lifecycle quality bars; stop-the-line on failed tests and maintain a precommitted constraints file. |
| [cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill) | ~74,070 stars; MIT; unarchived | Source/coverage ledger → candidate finding → isolated disproof → structured statuses → independent verification. Sandbox target-controlled processes; unsupported findings remain needs-validation. |
| [obsidianmd/obsidian-skills](https://github.com/obsidianmd/obsidian-skills) | ~52,966 stars; MIT; unarchived; `kepano/obsidian-skills` resolves here | Open Markdown/Bases/Canvas surfaces may inform *exports* from Second Brain, with rights, provenance, and no silent private-data copying. |
| [google/elephant-goldfish-model](https://github.com/google/elephant-goldfish-model) | ~20,452 stars; Apache-2.0; unarchived | Context-rich spec writer, context-isolated critic; human resolves design. Repo explicitly says it is **not an officially supported Google product**. No “independent” claim for same-context self-review. |

### Offensive framework review — architecture research only

[Google Threat Intelligence Group, Oct 30 2026](https://cloud.google.com/blog/topics/threat-intelligence/artex-and-friends-enter-agentic-offensive-frameworks/)
reports misuse of autonomous offensive security agents. See also the Nov 9
[GitGuardian taxonomy and exposure analysis](https://blog.gitguardian.com/ai-pentesting-tools-beyond-artex/).

| Source | Readback and concern | Safe SZL adaptation |
| --- | --- | --- |
| [oritera/Cairn](https://github.com/oritera/Cairn) | ~5,253 stars; AGPL-3.0; fact-intent-hint blackboard and dynamic state-space search; offensive primary use | **Facts vs hypotheses vs proposals** for read-only incident and verification planning; no exploit workers. |
| [vxcontrol/pentagi](https://github.com/vxcontrol/pentagi) | ~34,312; MIT; autonomous offensive team/execution | Defensive isolation checklist: per-task rights, tool/network allowlists, audit log and authorized scope. |
| [usestrix/strix](https://github.com/usestrix/strix) | ~76,094; Apache-2.0; web-app testing with potentially real effects | Offline mock negative controls and bounded target-neutral reports only. |
| [KeygraphHQ/shannon](https://github.com/KeygraphHQ/shannon) | ~51,994; AGPL-3.0; active exploit verification | Proof-of-impact *format*, but only within explicitly approved, non-networked lab fixtures. |
| [GreyDGL/PentestGPT](https://github.com/GreyDGL/PentestGPT) | ~16,450; MIT; LLM-guided penetration testing | Evidence-quality and refusal classes for defensive evaluations, not attack playbooks. |
| [0x4m4/hexstrike-ai](https://github.com/0x4m4/hexstrike-ai) | ~13,669; MIT; MCP-enabled execution of many security tools | Deny-by-default tool admission and parameter-bound audit, without importing offensive adapters. |
| [aliasrobotics/cai](https://github.com/aliasrobotics/cai) | ~9,861; archived | Explicit deprecation/retirement state; do not depend on an archived security engine. |

Popularity is not an effectiveness, security or maintenance qualification.
No source from these repositories was copied or executed.

## 3. New SZL code: offline adaptive harness-selection audit

`tools/harness_selection_audit.py` is a **stdlib-only readback** of an operator
supplied, bounded selection record with an internally computed SHA-256. This is *not*
independently source-pinned or authenticated. It accepts the exact schema
`szl.harness-selection-audit/v1`; malformed / duplicate / non-finite JSON,
missing coverage, incorrect denominators, wrong scorer identity and mutable
source revisions fail closed. A result retains the input SHA-256.

The audit checks pre-registered score minimums and alpha; scorer-positive and
scorer-negative controls; sequential rounds with explicit candidate hashes and
coverage; separate development and sealed-final dataset hashes; a frozen
grader; declared final-test nonexposure; exactly one final evaluation;
explicit baseline/candidate harness identities and matched compute budgets;
preregistered limits on regressions of previously solved tasks; and a
paired baseline/candidate item tally.

The **nominal** one-sided exact McNemar comparison on the final pair has
`w` candidate-only successes, `l` baseline-only successes, `n` total
items, `d = w+l` discordant items:

```text
delta = (w - l) / n
P_one-sided = 2^(-d) * sum_{k=w}^d binom(d, k)
```

This is a paired, predeclared comparison **only if** the final evaluation was
truly independent and once-only, the units are appropriate, and its provenance
can be externally confirmed. Self-reported booleans and hashes are not
independent attestation; neither statistical evidence nor a p-value grants
release authorization. The code intentionally reports
`release_authorized=false`, `independently_verified=false`,
`model_quality_established=false` and `production_ready=false` for every
record. An internally consistent candidate receives
`UNATTESTED_REVIEW_CANDIDATE`, not PASS or production-ready. Problems result
in `HOLD_INCOMPLETE_OR_CONTRADICTORY_EVIDENCE`.

Run without executing any model or agent:

```sh
python -B tools/harness_selection_audit.py path/to/operator-review.json
python -B tools/harness_selection_audit.py path/to/operator-review.json --output new-result.json
python -B -m unittest tests.test_harness_selection_audit -v
```

Output writes are exclusive; existing results cannot be overwritten. This is
**not** an optimizer, grader, external source verifier, statistical power
analysis, sandbox runner, model-weight modification or proof of transfer.

## 4. Bounded integration plan — no additional stack

1. Route proposal candidate hashes from existing `szl-skills` and Hatun
   into a **read-only evidence record**; use the existing mandated authority
   checks to authorize any later action. The audit itself has no keys or APIs.
2. Keep train/dev/test fixed by immutable dataset revision and task family.
   Freeze true independent tests before the model/harness search. Add repeat
   seeds, baseline compute/cost, regressions and preservation of previously
   solved tasks to a **separate** preregistered experiment.
3. Use Cloudflare's independent-finding pattern for *defensive* control audits
   and EGM's fresh review concept for separate contexts. No real-target
   offensive scanning, attack automation or untrusted third-party tool install.
4. Show effective verification coverage, unproved gates, run provenance,
   observed spend and rollback in the existing A11oy/Anatomy UI. Never
   replace Holo/telemetry governance with vanity “agent count” badges.
5. Treat the nested-field physics screenshot as `HYPOTHESIS_UNVERIFIED`
   until it generates quantitative, falsifiable, out-of-sample predictions.
   Lean kernel proof of a *formal statement* is distinct from empirical
   validation of a physical model.
6. Preserve repo guards: `szl-skills` currently has ~23 bytes of margin
   under its **skills/** source-size cap. This work lives under `tools/`,
   `tests/`, and `docs/`; it adds **no new installable skill**, avoids
   changing the curated marketplace, and weakens no source-size checks.

**Status:** Research and implementation are a source-only, self-reported
offline contract. No independent model run, purchase, training, deployment,
HF Space promotion, scientific finding or new production authority is claimed.
