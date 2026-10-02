---
name: szl-range-propagation
description: "Check whether a declared numeric range encloses every result of a bounded arithmetic expression on measurement or simulation intervals, with SI unit checks. Use for interval bounds through sums, differences, products, or ratios. Not for probability intervals or statistical uncertainty."
license: Apache-2.0
---

# Range propagation

Use this when a methods section or data pipeline reports an output range calculated from bounded inputs. The helper computes a conservative interval in SI units and asks whether each stated output bound contains it. It uses exact rational arithmetic over a small declared graph; it does not execute formulas supplied as code.

Run `python scripts/run.py assets/example.json --output range-report.json`. The invented rectangle example has length 1.9–2.1 m and width 0.9–1.1 m. Its area can range from 1.71 to 2.31 m², so the declared 1.8–2.2 m² bound fails. The CLI returns 1 and saves a receipt with the exact input-byte SHA-256. Exit 0 means all declared bounds contain the computed ranges; exit 1 means a failed claim or an unavailable range; exit 2 means invalid input or I/O failure.

For real work, prepare your own declaration using `references/contract.md`. Inspect findings and the SI dimension vector before citing a bound. Retain the original input and report together. A corrected declaration is a new audit, not evidence that the original passed.

This skill is a standalone companion to SZL's `szl-unit-invariants`, whose allowlisted units and SI dimension order it reuses from the Apache-2.0 v0.4.0 source. It has no network calls, credentials, or third-party packages. The mathematical basis is interval arithmetic, described in the [NIST Digital Library of Mathematical Functions](https://dlmf.nist.gov/3.1).

## Boundaries

Input ranges are assumed, not measured or authenticated. Conservative bounds can be loose when a variable appears more than once: `x - x` need not collapse to zero. This is not a statistical confidence interval, a coverage probability, an uncertainty budget, a symbolic proof, or a physical-law check. Offset and logarithmic units, unknown units, zero-crossing divisors, cycles, and unbounded numbers cannot pass. A hash binds bytes but is not a signature.
