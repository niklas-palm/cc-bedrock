"""Who counts as a person: a session of the Identity Center role (TELEMETRY_ROLES), which STS names after them."""

import fnmatch
import os
import re

NAME = re.compile(r"^[\w+=,.@-]{2,64}$")  # what STS allows in a role session name: no wildcards


def roles() -> list:
    return os.environ["TELEMETRY_ROLES"].split(",")


def is_person_role(role_name: str) -> bool:
    return any(fnmatch.fnmatchcase(role_name, pattern) for pattern in roles())


def principal_patterns() -> list:
    """The roles as SQL LIKE patterns for the bill's line_item_iam_principal (an assumed-role session ARN)."""
    return [f"%:assumed-role/{p.replace('*', '%').replace('?', '_')}/%" for p in roles()]
