"""Include the rare-disease skill's own tests in the repository CI suite."""

import pathlib
import unittest


SKILL_TESTS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "skills"
    / "szl-rare-disease-evidence-map"
    / "tests"
)


def load_tests(loader, suite, pattern):
    suite.addTests(unittest.TestLoader().discover(str(SKILL_TESTS), pattern="test_*.py"))
    return suite
