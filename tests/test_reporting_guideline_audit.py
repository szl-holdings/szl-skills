"""Fail-closed reporting maps. A complete item list is not compliance."""

import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "szl-reporting-guideline-audit"
AUDIT = runpy.run_path(str(SKILL / "scripts" / "run.py"))

PRISMA = (
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "10a", "10b", "11", "12",
    "13a", "13b", "13c", "13d", "13e", "13f", "14", "15", "16a", "16b", "17",
    "18", "19", "20a", "20b", "20c", "20d", "21", "22", "23a", "23b", "23c",
    "23d", "24a", "24b", "24c", "25", "26", "27",
)
CONSORT = (
    "1a", "1b", "2", "3", "4", "5a", "5b", "6", "7", "8", "9", "10", "11",
    "12a", "12b", "13", "14", "15", "16a", "16b", "17a", "17b", "18", "19",
    "20a", "20b", "21a", "21b", "21c", "21d", "22a", "22b", "23a", "23b",
    "24a", "24b", "25", "26", "27", "28", "29", "30",
)
STROBE = (
    "1a", "1b", "2", "3", "4", "5", "6a", "6b", "7", "8", "9", "10", "11",
    "12a", "12b", "12c", "12d", "12e", "13a", "13b", "13c", "14a", "14b",
    "14c", "15", "16a", "16b", "16c", "17", "18", "19", "20", "21", "22",
)


def _evaluate(payload: dict) -> dict:
    return AUDIT["evaluate"](json.dumps(payload).encode("utf-8"))


def _map(guideline: str, design: str | None = None, skip: set[str] | None = None) -> dict:
    skip = skip or set()
    items = []
    for item_id, kind, designs, _label in AUDIT["CHECKLISTS"][guideline]:
        if item_id in skip:
            continue
        if designs and (design is None or design not in designs.split(",")):
            continue
        item = {"id": item_id, "locator": "synthetic fixture"}
        if kind == "C":
            item["applicability"] = "applicable"
        items.append(item)
    payload = {"schema": AUDIT["SCHEMA"], "guideline": guideline, "items": items}
    if design is not None:
        payload["design"] = design
    return payload


class FrozenChecklistTests(unittest.TestCase):
    def test_ids_match_the_frozen_rows(self):
        self.assertEqual(AUDIT["ids"]("PRISMA_2020"), PRISMA)
        self.assertEqual(AUDIT["ids"]("CONSORT_2025"), CONSORT)
        self.assertEqual(AUDIT["ids"]("STROBE_2007"), STROBE)
        self.assertEqual(len(PRISMA), 42)
        self.assertEqual(len(CONSORT), 42)
        self.assertEqual(len(STROBE), 34)

    def test_conditional_rows_are_only_the_official_whole_item_conditions(self):
        def conditional(guideline):
            return {row[0] for row in AUDIT["CHECKLISTS"][guideline] if row[1] == "C"}

        self.assertEqual(conditional("PRISMA_2020"), set())
        self.assertEqual(conditional("CONSORT_2025"), {"12b", "20b", "23b"})
        self.assertEqual(conditional("STROBE_2007"), {"6b", "12d", "16c"})
        designs = {row[0]: row[2] for row in AUDIT["CHECKLISTS"]["STROBE_2007"]}
        self.assertEqual(designs["6b"], "cohort,case-control")
        self.assertEqual(designs["14c"], "cohort")
        self.assertEqual(designs["16c"], "")


class AuditTests(unittest.TestCase):
    def test_complete_prisma_map_is_not_compliance(self):
        report = _evaluate(_map("PRISMA_2020"))
        self.assertEqual(report["status"], "MAP_COMPLETE")
        self.assertEqual(report["reporting_compliance"], "NOT_EVALUATED")
        self.assertEqual(report["visual_confirmation"], "NOT_PERFORMED")
        self.assertEqual(report["readiness"], "HOLD")
        self.assertEqual(report["missing_ids"], [])
        self.assertEqual([item["disposition"] for item in report["items"]], ["LOCATED"] * 42)
        self.assertNotIn("COMPLIANT", json.dumps(report))

    def test_omitted_item_cannot_shrink_the_checklist_into_a_complete_map(self):
        report = _evaluate(_map("PRISMA_2020", skip={"27"}))
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["missing_ids"], ["27"])
        self.assertEqual(report["reporting_compliance"], "NOT_EVALUATED")
        self.assertEqual(len(report["items"]), 42)

    def test_required_item_cannot_be_waived_and_conditional_needs_a_decision(self):
        payload = _map("CONSORT_2025")
        payload["items"][0]["applicability"] = "not_applicable"
        payload["items"][0]["applicability_reason"] = "synthetic waiver"
        waived = _evaluate(payload)
        self.assertEqual(waived["status"], "BLOCKED")
        self.assertEqual(waived["findings"][0]["code"], "REQUIRED_ITEM_NOT_WAIVABLE")
        undecided = _map("CONSORT_2025")
        target = next(item for item in undecided["items"] if item["id"] == "12b")
        del target["applicability"]
        report = _evaluate(undecided)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["findings"][0]["code"], "CONDITIONAL_APPLICABILITY_REQUIRED")
        decided = _map("CONSORT_2025")
        target = next(item for item in decided["items"] if item["id"] == "20b")
        target["applicability"] = "not_applicable"
        target["applicability_reason"] = "open-label synthetic fixture"
        del target["locator"]
        report = _evaluate(decided)
        self.assertEqual(report["status"], "MAP_COMPLETE")
        row = next(item for item in report["items"] if item["id"] == "20b")
        self.assertEqual(row["disposition"], "NOT_APPLICABLE")
        self.assertEqual(report["visual_confirmation"], "NOT_PERFORMED")

    def test_strobe_design_drops_only_rows_the_combined_checklist_limits(self):
        report = _evaluate(_map("STROBE_2007", "cross-sectional"))
        self.assertEqual(report["status"], "MAP_COMPLETE")
        dropped = {item["id"] for item in report["items"] if item["disposition"] == "NOT_APPLICABLE_DESIGN"}
        self.assertEqual(dropped, {"6b", "14c"})
        self.assertEqual(report["reporting_compliance"], "NOT_EVALUATED")
        extra = _map("STROBE_2007", "cross-sectional")
        extra["items"].append({"id": "14c", "locator": "synthetic fixture"})
        self.assertEqual(_evaluate(extra)["findings"][0]["code"], "DESIGN_ITEM_NOT_IN_CHECKLIST")
        missing = _evaluate(_map("STROBE_2007", "cohort", skip={"14c"}))
        self.assertEqual(missing["status"], "INCOMPLETE")
        self.assertEqual(missing["missing_ids"], ["14c"])

    def test_superseded_and_unknown_guidelines_carry_no_checklist(self):
        old = _evaluate({"schema": AUDIT["SCHEMA"], "guideline": "CONSORT_2010", "items": []})
        self.assertEqual(old["status"], "BLOCKED")
        self.assertEqual(old["findings"][0]["code"], "UNSUPPORTED_GUIDELINE")
        self.assertEqual(old["items"], [])
        self.assertNotIn("1a", json.dumps(old["findings"]))
        unknown = _evaluate({"schema": AUDIT["SCHEMA"], "guideline": "SPIRIT_2025", "items": []})
        self.assertEqual(unknown["findings"][0]["code"], "UNKNOWN_GUIDELINE")

    def test_unknown_id_duplicate_and_oversize_input_are_blocked(self):
        payload = _map("PRISMA_2020")
        payload["items"].append({"id": "99", "locator": "synthetic fixture"})
        self.assertEqual(_evaluate(payload)["findings"][0]["code"], "UNKNOWN_ITEM")
        payload = _map("PRISMA_2020")
        payload["items"].append(dict(payload["items"][0]))
        self.assertEqual(_evaluate(payload)["findings"][0]["code"], "DUPLICATE_ITEM")
        report = AUDIT["evaluate"](b'{"schema":' + b" " * 70_000)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIsNone(report["input_sha256"])
        self.assertEqual(report["reporting_compliance"], "NOT_EVALUATED")

    def test_cli_example_exits_zero_and_refuses_to_replace_the_input(self):
        example = SKILL / "assets" / "example.json"
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"), str(example)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "MAP_COMPLETE")
        self.assertEqual(report["guideline"], "PRISMA_2020")
        original = example.read_bytes()
        self.assertEqual(report["input_sha256"], hashlib.sha256(original).hexdigest())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "report.json"
            written = subprocess.run(
                [sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"), str(example),
                 "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), written.stdout)
            replaced = subprocess.run(
                [sys.executable, "-I", "-B", str(SKILL / "scripts" / "run.py"), str(example),
                 "--output", str(example)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(replaced.returncode, 2)
            self.assertEqual(json.loads(replaced.stdout)["status"], "BLOCKED")
            self.assertEqual(example.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
