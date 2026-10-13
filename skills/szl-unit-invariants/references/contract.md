# Contract: szl.unit-invariants.v1

The top-level object has exactly `schema`, `quantities`, and `invariants`. Unknown fields are rejected. Every quantity has exactly `id`, `unit`, `dimension`, `values`, and `range_si`. Ids match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` and are unique. `values` is a nonempty array of at most 1000 numbers or decimal strings; booleans, NaN, infinity, expressions, and numbers outside zero or magnitude `1e-100..1e100` are rejected. Tokens contain at most 80 characters with an optional at most three-digit exponent. The entire record is bounded to 10000 quantity samples.

`dimension` is a seven-integer vector ordered **length, mass, time, electric current, thermodynamic temperature, amount of substance, luminous intensity**; each exponent is in `-8..8`. For example `m/s` is `[1,0,-1,0,0,0,0]`. A unit's table dimension is authoritative for arithmetic; disagreement with the declared dimension creates a finding. `range_si` is either null or exactly `{"min": decimal, "max": decimal}` in canonical SI units; bounds are inclusive.

The CLI validates each raw noninteger JSON numeric token before constructing a Decimal;
it never first rounds through binary64. Nonzero underflow such as `1e-400`, overflow,
out-of-bound exact values and overlong exponent spellings fail closed. Exact zero with
a supported exponent spelling remains zero. Integer-only fields remain integer-only:
numeric `1.0` does not become an integer dimension exponent or a string identifier.
The direct Python API also accepts Decimal values. A float supplied by a caller has
already lost any original lexical precision; use Decimal or decimal strings for exact
inputs. Arithmetic still uses the documented 50-digit context, rather than unlimited precision.

`input_sha256` binds sorted compact UTF-8 JSON with Decimal values represented by the
typed object `{"szl_decimal": "<Decimal str>"}`. This representation distinguishes a
Decimal number from a quoted decimal string; schema validation rejects caller objects
in numeric fields. Existing records without Decimal values retain their digest encoding.
`input_digest_encoding` names `canonical_json_with_decimal_tags.v1`. The CLI additionally
reports `input_bytes_sha256` of the exact supplied bytes, preserving distinctions in
whitespace and numeric lexeme spelling. Neither digest authenticates the evidence.

Allowlisted symbols and scale to SI:

| Symbols | Scales | SI dimension |
| --- | --- | --- |
| `1` | 1 | dimensionless |
| `m`, `cm`, `mm`, `km` | 1, 0.01, 0.001, 1000 | length |
| `kg`, `g`, `mg` | 1, 0.001, 0.000001 | mass |
| `s`, `ms`, `min`, `h` | 1, 0.001, 60, 3600 | time |
| `A`, `K`, `mol`, `cd` | 1 | corresponding SI base dimension |
| `Hz` | 1 | `[0,0,-1,0,0,0,0]` |
| `m/s`, `cm/s` | 1, 0.01 | `[1,0,-1,0,0,0,0]` |
| `m/s^2` | 1 | `[1,0,-2,0,0,0,0]` |
| `N`, `Pa`, `J`, `W` | 1 | respectively `[1,1,-2,0,0,0,0]`, `[-1,1,-2,0,0,0,0]`, `[2,1,-2,0,0,0,0]`, `[2,1,-3,0,0,0,0]` |

Units are case-sensitive exact symbols, not parsed expressions. `degC`, Fahrenheit, dB, percentages, dates, and arbitrary compound strings are unsupported. No affine offset is inferred.

Each invariant has exactly `id`, `operation`, `operands`, `result`, `atol_si`, and `rtol`. All references name known quantities. `equal` has one operand, `sum` has 2..100, and `difference`, `product`, `ratio` have two. The declared result array is compared to the operation on operand arrays, all of equal length with no broadcasting. Repeated operands are allowed to express, for example, a square via `product`. `equal`/`sum`/`difference` require compatible operand/result dimensions. `product` adds operand vectors and `ratio` subtracts them. Dimensional or shape failure skips numeric checking for that invariant. Zero denominators produce `UNDEFINED_RATIO`; they are never clamped.

The comparison is `abs(actual - expected) <= atol_si + rtol * abs(expected)`, with nonnegative absolute tolerance in result SI units and `rtol` in `0..1`. Tolerances are prespecified caller declarations. A fresh Decimal context uses 50 significant digits and round-half-even independently of ambient caller settings; inexact division and arithmetic are rounded in that context. This checker supplies no uncertainty model. If a failed comparison has two nonzero magnitudes whose larger/smaller ratio is at least 1000, it also emits `SCALE_MISMATCH`. Zero and sign errors still produce numerical findings without an invented scale ratio.

The report schema is `szl.unit-invariants-report.v1`; it contains sorted quantity/invariant reports and fixed finding fields `code`, `quantity`, `invariant`, `indexes`, `count`. Failure-index and detailed-case lists are capped at 100 while counts remain complete. Total invariant cases are limited to 10000. `PASS_DECLARED_CHECKS` means no declared check failed. `observed_units_verified` and `physical_law_verified` remain false. Malformed/unsupported input raises `ValueError`. The CLI exits 0 for a clean audit, 1 for a completed report with findings, and 2 for malformed input or IO failure; `--output` creates exclusively.

## Contract: szl.unit-invariants.v2

As v1, except `schema`, a strict `unit` expression and the `power` operation. Report schema `szl.unit-invariants-report.v2` adds each quantity's exact `si_scale` and each power invariant's `exponent`. v1 records, reports and digests are unchanged.

A unit is `1`, or factors joined by `*` with at most one `/` before a single final factor (`1/s`, `m/s^2`, `mol/L`, `kg*m^2/s^2`); it is parsed, never evaluated. A factor is a symbol with an optional exponent `^-8`..`^8` (not zero) applied to the prefixed symbol (`cm^2` is 1e-4 m^2). Bases: `m`, `g` (1e-3 kg), `s`, `A`, `K`, `mol`, `cd`, `L` (1e-3 m^3), `N`, `Pa`, `J`, `W`, `V`, `C`, `ohm`, `Hz`. Prefixes: `n`, `u` (ASCII micro), `m`, `c`, `k`, `M`. Longest match: a factor equal to a base is that base (`m`, `cd`, `mol`); otherwise it is one prefix plus a base (`mm` millimetre, `ms`, `mmol`, `kohm`). No implicit products: square metre is `m*m` or `m^2`. Rejected: `µ`, whitespace, parentheses, `+`, `min`, `h`, `m/s/s` and `J/kg*K` (write `J*kg^-1*K^-1`), `degC`, `dB`, `pH`. Bounds: 64 characters, at most 8 factors before `/` and one after it, composed SI exponents `-8..8`, scale an exact power of ten in `1e-36..1e36`.

A `power` invariant has the v1 fields plus `exponent` (JSON integer in `-8..8`, not zero) and one operand; `exponent` elsewhere is an unknown field. Its dimension is the operand vector times `exponent`; values use repeated 50-digit multiplication, then a reciprocal for negative exponents. A zero base with a negative exponent is `UNDEFINED_RATIO`. A case where (abs adjusted exponent of the base + 1) * abs(`exponent`) exceeds 250 is unsupported input (`ValueError`, CLI exit 2).
