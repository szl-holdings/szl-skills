# SPDX-License-Identifier: Apache-2.0
"""Exact, bounded interval arithmetic on declared SI quantities; no I/O or eval."""

from decimal import Decimal
from fractions import Fraction
import re


SCHEMA = "szl.range-propagation.v1"
REPORT_SCHEMA = "szl.range-propagation-report.v1"
ZERO = (0, 0, 0, 0, 0, 0, 0)
UNITS = {
    "1": (ZERO, "1"),
    "m": ((1, 0, 0, 0, 0, 0, 0), "1"),
    "cm": ((1, 0, 0, 0, 0, 0, 0), "0.01"),
    "mm": ((1, 0, 0, 0, 0, 0, 0), "0.001"),
    "km": ((1, 0, 0, 0, 0, 0, 0), "1000"),
    "kg": ((0, 1, 0, 0, 0, 0, 0), "1"),
    "g": ((0, 1, 0, 0, 0, 0, 0), "0.001"),
    "mg": ((0, 1, 0, 0, 0, 0, 0), "0.000001"),
    "s": ((0, 0, 1, 0, 0, 0, 0), "1"),
    "ms": ((0, 0, 1, 0, 0, 0, 0), "0.001"),
    "min": ((0, 0, 1, 0, 0, 0, 0), "60"),
    "h": ((0, 0, 1, 0, 0, 0, 0), "3600"),
    "A": ((0, 0, 0, 1, 0, 0, 0), "1"),
    "K": ((0, 0, 0, 0, 1, 0, 0), "1"),
    "mol": ((0, 0, 0, 0, 0, 1, 0), "1"),
    "cd": ((0, 0, 0, 0, 0, 0, 1), "1"),
    "Hz": ((0, 0, -1, 0, 0, 0, 0), "1"),
    "m/s": ((1, 0, -1, 0, 0, 0, 0), "1"),
    "cm/s": ((1, 0, -1, 0, 0, 0, 0), "0.01"),
    "m/s^2": ((1, 0, -2, 0, 0, 0, 0), "1"),
    "N": ((1, 1, -2, 0, 0, 0, 0), "1"),
    "Pa": ((-1, 1, -2, 0, 0, 0, 0), "1"),
    "J": ((2, 1, -2, 0, 0, 0, 0), "1"),
    "W": ((2, 1, -3, 0, 0, 0, 0), "1"),
}
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,12})?\Z")
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
OPERATIONS = frozenset(("sum", "difference", "product", "ratio"))


def szl_range_require(condition, code):
    if not condition:
        raise ValueError(code)


def szl_range_keys(value, names):
    szl_range_require(type(value) is dict and set(value) == set(names), "INVALID_FIELDS")


def szl_range_number(value):
    szl_range_require(type(value) in (str, int, Decimal), "BAD_NUMBER")
    token = str(value)
    szl_range_require(len(token) <= 32 and NUMBER.fullmatch(token) is not None, "BAD_NUMBER")
    result = Fraction(Decimal(token))
    szl_range_bound(result)
    return result


def szl_range_bound(value):
    szl_range_require(value.numerator.bit_length() <= 512 and value.denominator.bit_length() <= 512,
                      "COMPUTATION_LIMIT")
    return value


def szl_range_dimension(value):
    szl_range_require(type(value) is list and len(value) == 7 and
                      all(type(item) is int and -16 <= item <= 16 for item in value),
                      "BAD_DIMENSION")
    return tuple(value)


def szl_range_interval(lower, upper):
    lo, hi = szl_range_number(lower), szl_range_number(upper)
    szl_range_require(lo <= hi, "INVERTED_RANGE")
    return lo, hi


def szl_range_id(value):
    szl_range_require(type(value) is str and IDENTIFIER.fullmatch(value) is not None, "BAD_ID")
    return value


def szl_range_fraction_text(value):
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def szl_range_reported(item_id, value):
    dimension, lower, upper = value
    return {"id": item_id, "dimension": list(dimension),
            "lower_si": szl_range_fraction_text(lower), "upper_si": szl_range_fraction_text(upper)}


def szl_range_combine(operation, left, right):
    left_dim, a, b = left
    right_dim, c, d = right
    if operation in ("sum", "difference"):
        if left_dim != right_dim:
            return None, "INCOMPATIBLE_DIMENSIONS"
        dimension = left_dim
        bounds = (a + c, b + d) if operation == "sum" else (a - d, b - c)
    else:
        delta = 1 if operation == "product" else -1
        dimension = tuple(x + delta * y for x, y in zip(left_dim, right_dim))
        szl_range_require(all(-16 <= exponent <= 16 for exponent in dimension), "COMPUTATION_LIMIT")
        if operation == "ratio" and c <= 0 <= d:
            return None, "ZERO_CROSSING_DENOMINATOR"
        endpoints = (a * c, a * d, b * c, b * d) if operation == "product" else (
            a / c, a / d, b / c, b / d)
        bounds = (min(endpoints), max(endpoints))
    return (dimension, szl_range_bound(bounds[0]), szl_range_bound(bounds[1])), None


def szl_range_audit(record):
    """Return conservative SI intervals and assess caller-declared enclosing claims."""
    szl_range_keys(record, ("schema", "inputs", "steps", "claims"))
    szl_range_require(record["schema"] == SCHEMA, "BAD_SCHEMA")
    inputs, steps, claims = record["inputs"], record["steps"], record["claims"]
    szl_range_require(type(inputs) is list and 1 <= len(inputs) <= 64, "INPUT_COUNT")
    szl_range_require(type(steps) is list and 1 <= len(steps) <= 64, "STEP_COUNT")
    szl_range_require(type(claims) is list and 1 <= len(claims) <= 64, "CLAIM_COUNT")
    values, steps_parsed, claims_parsed, depths = {}, [], [], {}
    for item in inputs:
        szl_range_keys(item, ("id", "unit", "lower", "upper"))
        item_id = szl_range_id(item["id"])
        szl_range_require(item_id not in values, "DUPLICATE_ID")
        unit = item["unit"]
        szl_range_require(type(unit) is str and unit in UNITS, "UNKNOWN_UNIT")
        dim, scale = UNITS[unit]
        lo, hi = szl_range_interval(item["lower"], item["upper"])
        factor = Fraction(Decimal(scale))
        values[item_id] = (dim, szl_range_bound(lo * factor), szl_range_bound(hi * factor))
        depths[item_id] = 0
    step_ids = set()
    for item in steps:
        szl_range_keys(item, ("id", "operation", "operands"))
        item_id = szl_range_id(item["id"])
        szl_range_require(item_id not in depths, "DUPLICATE_ID")
        operation, operands = item["operation"], item["operands"]
        szl_range_require(type(operation) is str and operation in OPERATIONS, "UNKNOWN_OPERATION")
        szl_range_require(type(operands) is list and len(operands) == 2, "BAD_OPERANDS")
        szl_range_require(all(type(ref) is str and ref in depths for ref in operands),
                          "UNKNOWN_OR_FUTURE_REFERENCE")
        depth = max(depths[ref] for ref in operands) + 1
        szl_range_require(depth <= 32, "COMPUTATION_LIMIT")
        depths[item_id] = depth
        step_ids.add(item_id)
        steps_parsed.append((item_id, operation, operands))
    seen_claims = set()
    for item in claims:
        szl_range_keys(item, ("id", "dimension", "lower_si", "upper_si"))
        item_id = szl_range_id(item["id"])
        szl_range_require(item_id in step_ids and item_id not in seen_claims, "BAD_CLAIM_ID")
        seen_claims.add(item_id)
        claims_parsed.append((item_id, szl_range_dimension(item["dimension"]),
                              szl_range_interval(item["lower_si"], item["upper_si"])))
    report = {"schema": REPORT_SCHEMA, "status": "UNKNOWN", "steps": [], "claims": [],
              "findings": [], "scientific_truth_verified": False,
              "probabilistic_coverage_established": False}
    for item_id, operation, operands in steps_parsed:
        value, failure = szl_range_combine(operation, values[operands[0]], values[operands[1]])
        if failure:
            report["findings"].append({"id": item_id, "code": failure})
            return report
        values[item_id] = value
        report["steps"].append(szl_range_reported(item_id, value))
    for item_id, claim_dimension, (claimed_lo, claimed_hi) in claims_parsed:
        actual_dimension, actual_lo, actual_hi = values[item_id]
        if claim_dimension != actual_dimension:
            code = "DIMENSION_MISMATCH"
        elif claimed_lo > actual_lo or claimed_hi < actual_hi:
            code = "CLAIM_DOES_NOT_CONTAIN_RANGE"
        else:
            code = "CONTAINS_COMPUTED_RANGE"
        claim = {"id": item_id, "status": code,
                 "claimed_dimension": list(claim_dimension),
                 "claimed_lower_si": szl_range_fraction_text(claimed_lo),
                 "claimed_upper_si": szl_range_fraction_text(claimed_hi)}
        report["claims"].append(claim)
        if code != "CONTAINS_COMPUTED_RANGE":
            report["findings"].append({"id": item_id, "code": code})
    report["status"] = "FAIL_DECLARED_BOUNDS" if report["findings"] else "PASS_DECLARED_BOUNDS"
    return report


audit = szl_range_audit
