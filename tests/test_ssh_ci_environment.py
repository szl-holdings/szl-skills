"""Deployment policy validation, independent of live GitHub configuration."""

import unittest
from unittest import mock

from tools.ssh_ci_environment import main_branch_policy


class PolicyTests(unittest.TestCase):
    def test_documented_list_shape_resolves_type_by_same_policy_id(self):
        payload = {"total_count": 1, "branch_policies": [
            {"id": 361472, "node_id": "policy-node", "name": "main"}]}
        lookup = mock.Mock(return_value={"id": 361472, "name": "main", "type": "branch"})
        self.assertTrue(main_branch_policy(payload, lookup=lookup))
        lookup.assert_called_once_with(361472)
        for detail in ({"id": 361472, "name": "main", "type": "tag"},
                       {"id": 361472, "name": "main"},
                       {"id": 7, "name": "main", "type": "branch"},
                       {"id": 361472, "name": "release", "type": "branch"}, None):
            with self.subTest(detail=detail), self.assertRaises(ValueError):
                main_branch_policy(payload, lookup=lambda _id: detail)

    def test_exact_main_branch_and_empty_bootstrap(self):
        self.assertTrue(main_branch_policy({"total_count": 1, "branch_policies": [
            {"id": 17, "name": "main", "type": "branch"}]}))
        self.assertFalse(main_branch_policy({"total_count": 0, "branch_policies": []},
                                           allow_empty=True))

    def test_missing_paginated_or_broader_policy_is_rejected(self):
        main = {"name": "main", "type": "branch"}
        for payload in (None, {}, {"total_count": True, "branch_policies": [main]},
                        {"total_count": 0, "branch_policies": []},
                        {"total_count": 2, "branch_policies": [main]},
                        {"total_count": 2, "branch_policies": [main, main]},
                        {"total_count": 1, "branch_policies": ["main"]},
                        {"total_count": 1, "branch_policies": [{"name": "main"}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                main_branch_policy(payload)
