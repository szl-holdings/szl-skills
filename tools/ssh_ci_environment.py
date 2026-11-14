"""Validate the isolated lane's explicit main-branch deployment policy."""


def main_branch_policy(payload, *, allow_empty=False, lookup=None):
    """Verify one main/branch rule, resolving an omitted type by authenticated ID lookup."""
    if type(payload) is not dict:
        raise ValueError("deployment branch policy response is not an object")
    count, rules = payload.get("total_count"), payload.get("branch_policies")
    if type(count) is not int or type(rules) is not list or count != len(rules):
        raise ValueError("deployment branch policy listing is incomplete")
    if allow_empty and count == 0:
        return False
    if count != 1 or type(rules[0]) is not dict or rules[0].get("name") != "main":
        raise ValueError("environment must allow exactly the main branch, with no tags or wildcards")
    rule = rules[0]
    # GitHub's response schema includes type, but its list example omits it.
    # Never infer branch vs tag from a name; resolve the same policy by ID.
    if "type" not in rule and lookup is not None:
        policy_id = rule.get("id")
        if type(policy_id) is not int or policy_id <= 0:
            raise ValueError("deployment policy type is absent and its ID is invalid")
        detail = lookup(policy_id)
        if (type(detail) is not dict or type(detail.get("id")) is not int
                or detail["id"] != policy_id or detail.get("name") != rule["name"]):
            raise ValueError("deployment policy detail does not match the listed policy")
        rule = detail
    if rule.get("type") != "branch":
        raise ValueError("environment must allow exactly the main branch; policy type is not verified")
    return True


def required_environment_preimage(environment, branch_policies, secrets, *,
                                  expected_name, required_secret, lookup=None):
    """Validate existing protections and return a value safe to hash as a preimage.

    The bring-up controller is deliberately unable to create or replace an
    environment or upload a private key.  An owner provisions those separately;
    this validator proves the exact environment, required-reviewer gate,
    main-only deployment rule, and secret name already exist before variable
    writes are considered.  Secret values are never returned by GitHub and are
    never part of this structure.
    """
    if type(environment) is not dict or environment.get("name") != expected_name:
        raise ValueError("the pre-provisioned environment is missing or has the wrong name")
    policy = environment.get("deployment_branch_policy")
    if policy != {"protected_branches": False, "custom_branch_policies": True}:
        raise ValueError("environment must use a custom main-only deployment policy")
    rules = environment.get("protection_rules")
    if type(rules) is not list:
        raise ValueError("environment protection rules are unavailable")
    reviewer_rules = [rule for rule in rules
                      if type(rule) is dict and rule.get("type") == "required_reviewers"]
    if len(reviewer_rules) != 1:
        raise ValueError("environment must have exactly one required-reviewers rule")
    reviewers = reviewer_rules[0].get("reviewers")
    if (type(reviewers) is not list or not reviewers
            or any(type(item) is not dict or item.get("type") not in {"User", "Team"}
                   or type(item.get("reviewer")) is not dict
                   or type(item["reviewer"].get("id")) is not int
                   or item["reviewer"]["id"] <= 0 for item in reviewers)):
        raise ValueError("environment required reviewers are incomplete")
    main_branch_policy(branch_policies, lookup=lookup)
    if type(secrets) is not dict:
        raise ValueError("environment secret listing is not an object")
    count, rows = secrets.get("total_count"), secrets.get("secrets")
    if (type(count) is not int or type(rows) is not list or count != len(rows)
            or any(type(row) is not dict or type(row.get("name")) is not str
                   or type(row.get("created_at")) is not str
                   or type(row.get("updated_at")) is not str for row in rows)):
        raise ValueError("environment secret listing is incomplete")
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names) or required_secret not in names:
        raise ValueError("the dedicated CI secret is not pre-provisioned")
    return {"environment": environment, "branch_policies": branch_policies,
            "secrets": sorted(rows, key=lambda row: row["name"])}


def environment_variables_preimage(payload):
    """Return a complete, stable-order environment-variable listing and values."""
    if type(payload) is not dict:
        raise ValueError("environment variable listing is not an object")
    count, rows = payload.get("total_count"), payload.get("variables")
    if (type(count) is not int or type(rows) is not list or count != len(rows)
            or any(type(row) is not dict or type(row.get("name")) is not str
                   or type(row.get("value")) is not str for row in rows)):
        raise ValueError("environment variable listing is incomplete")
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        raise ValueError("environment variable listing contains duplicate names")
    ordered = sorted(rows, key=lambda row: row["name"])
    return {"total_count": count, "variables": ordered}, {
        row["name"]: row["value"] for row in ordered}
