"""Deployment policy validation, independent of live GitHub configuration."""

import unittest
from unittest import mock

from tools.ssh_ci_environment import (environment_variables_preimage, main_branch_policy,
                                      required_environment_preimage)


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


class EnvironmentPreimageTests(unittest.TestCase):
    def fixtures(self):
        environment = {
            "id": 41, "name": "massed-cpu-build",
            "deployment_branch_policy": {
                "protected_branches": False, "custom_branch_policies": True},
            "protection_rules": [{
                "id": 9, "type": "required_reviewers", "prevent_self_review": True,
                "reviewers": [{"type": "User", "reviewer": {"id": 4242,
                                                               "login": "owner"}}],
            }],
        }
        policies = {"total_count": 1, "branch_policies": [
            {"id": 17, "name": "main", "type": "branch"}]}
        secrets = {"total_count": 1, "secrets": [
            {"name": "SZL_CPU_SSH_KEY", "created_at": "2029-01-01T00:00:00Z",
             "updated_at": "2030-01-01T00:00:00Z"}]}
        return environment, policies, secrets

    def validate(self, environment, policies, secrets):
        return required_environment_preimage(
            environment, policies, secrets, expected_name="massed-cpu-build",
            required_secret="SZL_CPU_SSH_KEY")

    def test_existing_reviewer_policy_and_secret_are_retained_in_preimage(self):
        environment, policies, secrets = self.fixtures()
        result = self.validate(environment, policies, secrets)
        self.assertIs(result["environment"], environment)
        self.assertIs(result["branch_policies"], policies)
        self.assertEqual(result["secrets"], secrets["secrets"])
        self.assertTrue(environment["protection_rules"][0]["prevent_self_review"])

    def test_missing_or_ambiguous_authority_state_is_rejected(self):
        for mutation in (
                lambda e, p, s: e.update(name="other"),
                lambda e, p, s: e.update(deployment_branch_policy={
                    "protected_branches": True, "custom_branch_policies": False}),
                lambda e, p, s: e.update(protection_rules=[]),
                lambda e, p, s: e["protection_rules"].append(e["protection_rules"][0].copy()),
                lambda e, p, s: e["protection_rules"][0].update(reviewers=[]),
                lambda e, p, s: p["branch_policies"][0].update(name="*"),
                lambda e, p, s: s.update(total_count=0, secrets=[]),
                lambda e, p, s: s["secrets"].append(s["secrets"][0].copy()),
        ):
            environment, policies, secrets = self.fixtures()
            mutation(environment, policies, secrets)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(environment, policies, secrets)


class EnvironmentVariablePreimageTests(unittest.TestCase):
    def test_complete_listing_is_sorted_and_values_are_exposed_for_exact_comparison(self):
        payload = {"total_count": 2, "variables": [
            {"name": "Z_LAST", "value": "z", "updated_at": "2030-01-01T00:00:00Z"},
            {"name": "A_FIRST", "value": "a", "updated_at": "2030-01-01T00:00:00Z"},
        ]}
        preimage, values = environment_variables_preimage(payload)
        self.assertEqual([row["name"] for row in preimage["variables"]],
                         ["A_FIRST", "Z_LAST"])
        self.assertEqual(values, {"A_FIRST": "a", "Z_LAST": "z"})

    def test_incomplete_duplicate_or_untyped_values_are_rejected(self):
        fixtures = (
            None,
            {"total_count": 2, "variables": [{"name": "A", "value": "a"}]},
            {"total_count": 2, "variables": [
                {"name": "A", "value": "a"}, {"name": "A", "value": "b"}]},
            {"total_count": 1, "variables": [{"name": "A", "value": True}]},
        )
        for payload in fixtures:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                environment_variables_preimage(payload)
