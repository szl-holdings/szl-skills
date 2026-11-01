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
