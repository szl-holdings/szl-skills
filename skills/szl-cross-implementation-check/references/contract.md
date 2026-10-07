# Cross-implementation contract

Sides `a` and `b`: `{label?, implementation?, input_sha256?, results: object}`. `results` is flattened to
dotted keys (`effect.estimate`, `coefs[0]`).

Numeric comparison: CONSISTENT when `|a-b| <= max(atol, rtol*max(|a|,|b|))`, else DIVERGENT. Strings and
booleans: exact. One-sided, non-finite or mixed-type values: INCOMPARABLE. Per-quantity tolerances override
the global ones by flattened key.

Overall: DIVERGENT > INCOMPARABLE > CONSISTENT, and differing declared input digests force INCOMPARABLE
even when every quantity agrees. `document_sha256` is the canonical digest of the comparison document.

## Binding, overrides, stability (base b8e259c)

Changed: if exactly one side declares `input_sha256`, the report adds `"reason":
"INPUT_BINDING_INCOMPLETE"` and is INCOMPARABLE unless DIVERGENT (before, it could be CONSISTENT). A
quantity null or absent on both sides has reason `NULL_ON_BOTH_SIDES`.

ERROR, in order, `keys` naming offenders: `FLATTEN_COLLISION` (`{"x.y": 1, "x": {"y": 2}}`); global
`atol`/`rtol` not finite non-negative numbers (now also booleans, strings, null); `INVALID_OVERRIDE`
(an override is not an object of finite non-negative numeric `atol`/`rtol` only);
`UNKNOWN_OVERRIDE_KEY` (an override names no reported quantity). The CLI rejects duplicate keys and
NaN/Infinity.

Stability (`"schema": "szl.cross-implementation.stability.v1"`): `required_digits` int >= 1 and
>= 3 `runs` of `{input_sha256, perturbation, results}`; each `perturbation` distinct, non-empty,
keys from `precision`, `threads`, `summation_order`, `seed`. Values (decimal strings, or numbers via
shortest repr) become fractions. Per quantity: exact `min`/`max`/`median`, `relative_spread` =
(max-min)/max|v|, `agreed_digits` = largest d with relative_spread <= 10^-d (null if identical),
`sign_consistent`, `cancellation` if 10|median| <= max|v|. STABLE if agreed_digits is null or >=
required_digits, else UNSTABLE (`BELOW_REQUIRED_DIGITS` / `NOT_NUMERIC_IN_EVERY_RUN`). Overall
UNSTABLE > INCOMPARABLE (missing run digest: INPUT_BINDING_INCOMPLETE; differing digests; nothing to
compare) > STABLE; `issues` lists UNSTABLE quantities.
