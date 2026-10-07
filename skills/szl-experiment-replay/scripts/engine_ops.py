#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""v2 column operations, pinned apart from the unchanged v1 engine.py."""

import csv
import hashlib
import io
import pathlib
import runpy
import types
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction

E = runpy.run_path(str(pathlib.Path(__file__).resolve().parent / "engine.py"))  # trusted v1 helpers
SCHEMA, RECEIPT_SCHEMA = "szl.experiment-replay.pin.v2", "szl.experiment-replay.receipt.v2"
DECLARATION_SCHEMA, REFERENCE_SCHEMA = "szl.experiment-replay.declaration.v2", "szl.experiment-replay.reference.v2"
SUM, COUNT, MEDIAN, VARIANCE, GROUPED = OPERATIONS = (
    "csv_column_sum_v1", "csv_column_count_v1", "csv_column_median_v1", "csv_column_variance_v1", "csv_grouped_mean_v1")
FIELDS = {"schema", "operation", "input", "reference", "column", "absolute_tolerance", "engine_sha256", "engine_ops_sha256", "pin_sha256"}


def _v1(name, **names):  # reuse v1 code verbatim with v2 globals
    return types.FunctionType(E[name].__code__, {**E[name].__globals__, **names}, name, E[name].__defaults__)


_ops_hash = _v1("_engine_hash", __file__=__file__)  # hashes this file
_receipt = _v1("_receipt", RECEIPT_SCHEMA=RECEIPT_SCHEMA)


def _load(operation, raw, reference_raw, column, group):
    E["_sum_column"](raw, column)  # all v1 CSV checks
    header, *rows = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
    if group is not None and group not in header:
        raise ValueError("group column absent from CSV")
    groups = {}
    for row in rows:
        label = column if group is None else E["_column"](row[header.index(group)])
        groups.setdefault(label, []).append(row[header.index(column)])
    ref = E["_read_json"](reference_raw)
    values = ref.get("values") if isinstance(ref, dict) else None
    if (ref != {"schema": REFERENCE_SCHEMA, "operation": operation, "values": values} or not isinstance(values, dict)
            or values.keys() != groups.keys() or operation == VARIANCE and len(rows) < 2
            or not all(isinstance(v, str) and E["DECIMAL"].fullmatch(v) for v in values.values())):
        raise ValueError("reference or rows do not fit the operation")
    return groups, values


def _decimal_path(operation, texts):  # exact (numerator, denominator); one-pass variance
    xs = sorted(map(Decimal, texts))
    n, half, total, squares = Decimal(len(xs)), len(xs) // 2, Decimal(0), Decimal(0)
    for x in xs:
        total, squares = total + x, squares + x * x
    if operation == MEDIAN:
        return (xs[half], 1) if len(xs) % 2 else (xs[half - 1] + xs[half], 2)
    return {SUM: (total, 1), COUNT: (n, 1), GROUPED: (total, n), VARIANCE: (n * squares - total * total, n * (n - 1))}[operation]


def _fraction_path(operation, texts):  # independent; two-pass variance
    xs = sorted(map(Fraction, texts))
    n, total = len(xs), sum(xs, Fraction(0))
    if operation == MEDIAN:
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    if operation == VARIANCE:
        return sum(((x - total / n) ** 2 for x in xs), Fraction(0)) / (n - 1)
    return {SUM: total, COUNT: Fraction(n), GROUPED: total / n}[operation]


def _text(value):
    try:  # exact decimal under the traps, else p/q
        return format(Decimal(value.numerator) / value.denominator, "f")
    except (Inexact, Rounded):
        return str(value)


def _evaluate(operation, raw, reference_raw, column, group, tolerance):
    groups, expected = _load(operation, raw, reference_raw, column, group)
    results, agree = [], True
    with localcontext() as context:  # default traps plus exactness
        context.prec, context.traps[Inexact], context.traps[Rounded] = 400, True, True
        for label in sorted(groups):
            num, den = _decimal_path(operation, groups[label])
            value, want = _fraction_path(operation, groups[label]), Fraction(expected[label])
            agree &= Fraction(num) / Fraction(den) == value
            results.append({"label": label, "row_count": len(groups[label]), "observed": _text(value), "expected": expected[label],
                            "absolute_error": _text(abs(value - want)), "within": abs(value - want) <= Fraction(tolerance)})
    return results, agree


def _validate_pin(pin):
    if not isinstance(pin, dict) or pin.get("schema") != SCHEMA or pin.get("pin_sha256") != E["_digest"]({k: v for k, v in pin.items() if k != "pin_sha256"}):
        raise ValueError("invalid v2 pin or pin digest")
    if pin.get("operation") not in OPERATIONS:
        raise NotImplementedError("unsupported operation")
    grouped, ops = pin["operation"] == GROUPED, pin.get("engine_ops_sha256")
    if (pin.keys() != FIELDS | ({"group_column"} if grouped else set()) or not isinstance(ops, str) or not E["SHA256"].fullmatch(ops)
            or grouped and E["_column"](pin["group_column"]) == pin["column"]):
        raise ValueError("invalid v2 pin fields")
    # v1 rules for file pins, paths, column, tolerance and engine digest
    view = {key: pin[key] for key in ("input", "reference", "column", "absolute_tolerance", "engine_sha256")}
    view.update(schema=E["SCHEMA"], operation=E["OPERATION"])
    view["pin_sha256"] = E["_digest"](view)
    E["_validate_pin"](view)


def prepare(root, declaration):
    if not isinstance(declaration, dict) or declaration.get("schema") != DECLARATION_SCHEMA or (
            declaration.keys() & {"input", "reference", "engine_sha256", "engine_ops_sha256", "pin_sha256"}):
        raise ValueError("unsupported declaration or fields")
    pin = {key: value for key, value in declaration.items() if key not in ("input_path", "reference_path")}
    pin.update(schema=SCHEMA, input=E["_file_pin"](root, declaration.get("input_path"), E["MAX_CSV"])[0],
               reference=E["_file_pin"](root, declaration.get("reference_path"), E["MAX_REFERENCE"])[0],
               engine_sha256=E["_engine_hash"](), engine_ops_sha256=_ops_hash())
    pin["pin_sha256"] = E["_digest"](pin)
    trial = replay(root, pin)  # same checks and arithmetic as a later replay
    if trial["status"] not in ("MATCH", "DIVERGED"):
        raise ValueError("declaration refused: " + trial["reason"])
    return pin


def replay(root, pin):
    try:
        _validate_pin(pin)
    except NotImplementedError:
        return _receipt(None, "REFUSED", "UNSUPPORTED_OPERATION")
    except (ValueError, TypeError, OverflowError, RecursionError):
        return _receipt(None, "REFUSED", "INVALID_PIN")
    for name, digest in (("ENGINE", E["_engine_hash"]), ("ENGINE_OPS", _ops_hash)):
        key = name.lower() + "_sha256"
        try:
            seen = digest()
        except (OSError, ValueError):
            return _receipt(pin, "INCOMPLETE", name + "_UNREADABLE")
        if seen != pin[key]:
            return _receipt(pin, "INCOMPLETE", name + "_CHANGED", **{key + "_observed": seen})
    observed, raws = {}, {}
    for role, limit in (("input", E["MAX_CSV"]), ("reference", E["MAX_REFERENCE"])):
        try:
            raws[role] = E["read_file"](root, pin[role]["path"], limit)
            observed[role] = hashlib.sha256(raws[role]).hexdigest()
            reason = "_CHANGED" if (observed[role], len(raws[role])) != (pin[role]["sha256"], pin[role]["bytes"]) else ""
        except FileNotFoundError:
            reason = "_MISSING"
        except (OSError, ValueError):
            reason = "_UNSAFE_OR_UNREADABLE"
        if reason:
            return _receipt(pin, "INCOMPLETE", role.upper() + reason, observed_hashes=observed)
    try:
        results, agree = _evaluate(pin["operation"], raws["input"], raws["reference"], pin["column"], pin.get("group_column"), pin["absolute_tolerance"])
    except (ValueError, ArithmeticError, RecursionError, csv.Error):
        return _receipt(pin, "REFUSED", "PINNED_CONTENT_INVALID", observed_hashes=observed)
    details = {"operation": pin["operation"], "observed_hashes": observed, "row_count": sum(r["row_count"] for r in results)}
    if not agree:
        return _receipt(pin, "INCOMPLETE", "INTERNAL_DISAGREEMENT", "COMPLETED", **details)
    ok = all(r["within"] for r in results)
    return _receipt(pin, "MATCH" if ok else "DIVERGED", "WITHIN_TOLERANCE" if ok else "OUTSIDE_TOLERANCE", "COMPLETED", **details,
                    results=results, arithmetic_paths=["DECIMAL", "FRACTION"], absolute_tolerance=pin["absolute_tolerance"])
