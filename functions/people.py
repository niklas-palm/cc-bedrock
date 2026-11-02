"""Who counts as a person: a session of the Identity Center role (TELEMETRY_ROLES), which STS names after them."""

import fnmatch
import os
import re

NAME = re.compile(r"[\w+=,.@-]{2,64}")  # what STS allows in a role session name, no wildcards (fullmatch)


def roles() -> list:
    return os.environ["TELEMETRY_ROLES"].split(",")


def is_person_role(role_name: str) -> bool:
    return any(fnmatch.fnmatchcase(role_name, pattern) for pattern in roles())


def principal_patterns() -> list:
    """The roles as SQL LIKE patterns for the bill's line_item_iam_principal (an assumed-role session ARN)."""
    def like(pattern):  # a literal _ (AWSReservedSSO_) or % in a role name isn't a wildcard
        escaped = pattern.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
        return escaped.replace("*", "%").replace("?", "_")
    return [f"%:assumed-role/{like(p)}/%" for p in roles()]


# The person in a bill line: line_item_iam_principal is arn:aws:sts::<account>:assumed-role/<role>/<session>
BILLED_USER = "regexp_extract(line_item_iam_principal, '[^/]+$')"


def billed_lines(first_period: str, last_period: str):
    """SQL and parameters for the bill's Bedrock lines from people's sessions, in billing periods (YYYY-MM)
    first_period to last_period. Shared by the dashboard and the budgets, so their billed figures agree."""
    roles = principal_patterns()
    sql = ("billing_period BETWEEN ? AND ? AND line_item_product_code IN ('AmazonBedrock', 'AmazonBedrockService')"
           " AND (" + " OR ".join(["line_item_iam_principal LIKE ? ESCAPE '\\'"] * len(roles)) + ")")
    return sql, [first_period, last_period, *roles]
