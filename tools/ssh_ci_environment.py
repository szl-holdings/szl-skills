"""Pure validation of the isolated lane's explicit main-branch deployment policy."""


def main_branch_policy(payload, *, allow_empty=False):
    """Return whether exactly one main/branch rule exists; reject broader policies."""
    if type(payload) is not dict:
        raise ValueError("deployment branch policy response is not an object")
    count, rules = payload.get("total_count"), payload.get("branch_policies")
    if type(count) is not int or type(rules) is not list or count != len(rules):
        raise ValueError("deployment branch policy listing is incomplete")
    if allow_empty and count == 0:
        return False
    if (count != 1 or type(rules[0]) is not dict or rules[0].get("name") != "main"
            or rules[0].get("type") != "branch"):
        raise ValueError("environment must allow exactly the main branch, with no tags or wildcards")
    return True
