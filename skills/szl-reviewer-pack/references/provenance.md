# Provenance

The unresolved-status extension (`UNRESOLVED_STATUSES`, the `missed`, `not_tested`,
`inference_blockers` and `incomparable` finding fields) and the `assets/status-probe/` fixtures are an
original SZL contribution under Apache-2.0, written against base commit b8e259c. No third-party code
was copied. The status names are taken from the report vocabularies of this repository's own skills
(current kernels plus the fixed names reserved for their upgrades). The fixtures are trimmed copies of
synthetic reports from those skills' kernels: the mutation and evidence-gate ones on their bundled
examples, the cross-implementation ones on its bundled example with side b's results set to side a's
(plus a one-sided `aic` quantity for INCOMPARABLE). They carry no external or personal data.
