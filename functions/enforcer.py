"""Applies the admins' controls to Bedrock, through the `<name>-controls` IAM policy that the Identity Center
permission set includes: whatever it denies, Bedrock refuses at once, from any client.

  Budgets  people over a daily, weekly or monthly limit are denied until the period ends, an admin raises the
           limit, or an admin resets them (their spend so far in the current periods is forgiven).
  Models   models the admins turned off are denied for everyone.

Runs every 10 minutes, and at once when an admin changes something ({"reset": "<user>" or "*"} to reset).
Spend is the larger of two figures: Claude Code's own estimate from telemetry (current to the minute, list prices)
and the bill (CUR 2.0 by IAM principal: exact, but up to a day behind), so missing telemetry can't escape a budget
for long. Periods are UTC; weeks start on Monday. The deny matches `aws:userid`, which for an Identity Center
session is `<role id>:<user name>`.
"""

import datetime
import json
import os
import re
import sys

import boto3

sys.path.insert(0, os.path.dirname(__file__))  # Lambda loads us as functions/<name>: our siblings
import athena  # noqa: E402
import people  # noqa: E402

iam = boto3.client("iam")
s3 = boto3.client("s3")
BUCKET = os.environ.get("BUCKET", "")
POLICY_ARN = os.environ.get("POLICY_ARN", "")
PERIODS = ("daily", "weekly", "monthly")
NOBODY = "nobody-is-over-budget"
MODEL = re.compile(r"^anthropic\.[a-z0-9.:-]+$")
INVOKE = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]


def handler(event, context):
    budgets = load("config/budgets.json") or {"default": {}, "users": {}}
    models = load("config/models.json") or {"disabled": []}
    forgiven = load("state/forgiven.json") or {}
    today = datetime.datetime.now(datetime.UTC).date()
    starts = {"daily": today, "weekly": today - datetime.timedelta(days=today.weekday()),
              "monthly": today.replace(day=1)}

    spend = spending(starts, today)
    if event.get("reset"):
        forgiven = reset(forgiven, spend, starts, event["reset"])
        save("state/forgiven.json", forgiven)
    users, blocked = assess(budgets, forgive(spend, forgiven, starts))
    changed = apply(policy(blocked, models.get("disabled", [])))
    save("state/budgets.json", {"updated": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                                "blocked": blocked, "users": users})
    return {"blocked": blocked, "policy_changed": changed}


def reset(forgiven: dict, spend: dict, starts: dict, who: str) -> dict:
    """Forgive what someone (or "*", everyone) has spent so far in the current periods: their budgets start again
    from now, as they would when a period ends. A forgiveness lasts until its period ends."""
    for key in spend if who == "*" else [who.lower()]:
        if key in spend:
            forgiven[key] = {p: [starts[p].isoformat(), spend[key][p]] for p in PERIODS}
    return forgiven


def forgive(spend: dict, forgiven: dict, starts: dict) -> dict:
    """Spend with what was forgiven in the current periods taken off."""
    result = {}
    for key, entry in spend.items():
        result[key] = dict(entry)
        for p in PERIODS:
            period, amount = forgiven.get(key, {}).get(p, [None, 0])
            if period == starts[p].isoformat():
                result[key][p] = max(0.0, entry[p] - amount)
    return result


def assess(budgets: dict, spend: dict):
    """Everyone's spend against their limits ({user: status}), and the session names to deny.

    People are keyed by their lower-cased name, the way budgets are; the deny lists every spelling seen, since IAM
    compares case-sensitively. A budget on someone who hasn't spent yet can block them too (a limit of 0)."""
    users = {}
    for key in set(spend) | set(budgets.get("users", {})):
        limits = {**budgets.get("default", {}), **budgets.get("users", {}).get(key, {})}
        entry = spend.get(key) or {"names": [key], **{p: 0.0 for p in PERIODS}}
        spent = {p: entry[p] for p in PERIODS}
        over = [p for p in PERIODS if limits.get(p) is not None and spent[p] >= limits[p]]
        users[key] = {"user": entry["names"][0], "names": entry["names"], "spent": spent, "limits": limits,
                      "over": over}
    blocked = sorted({name for u in users.values() if u["over"] for name in u["names"] if people.NAME.match(name)})
    return users, blocked


def spending(starts: dict, today: datetime.date) -> dict:
    """{lower-cased user: {names, daily, weekly, monthly}}, each the larger of the estimate and the bill."""
    first = min(starts.values())

    def sums(day_expression):
        return ", ".join(f"sum(if({day_expression} >= '{starts[p].isoformat()}', cost)) AS {p}" for p in PERIODS)

    roles = people.principal_patterns()
    results = athena.run({
        "estimated": (f"""SELECT "user", {sums('dt')} FROM (
                SELECT "user", dt, value AS cost FROM telemetry
                WHERE dt BETWEEN ? AND ? AND name = 'claude_code.cost.usage') GROUP BY 1""",
            [first.isoformat(), today.isoformat()]),
        "billed": (f"""SELECT "user", {sums('day')} FROM (
                SELECT regexp_extract(line_item_iam_principal, '[^/]+$') AS "user",
                    CAST(date(line_item_usage_start_date) AS varchar) AS day, line_item_unblended_cost AS cost
                FROM cur WHERE billing_period BETWEEN ? AND ? AND line_item_product_code LIKE 'AmazonBedrock%'
                    AND (""" + " OR ".join(["line_item_iam_principal LIKE ?"] * len(roles)) + ")) GROUP BY 1",
            [first.isoformat()[:7], today.isoformat()[:7], *roles]),
    })
    spend = {}
    for source in ("estimated", "billed"):
        for row in results[source]:
            entry = spend.setdefault(row["user"].lower(), {"names": [], **{p: 0.0 for p in PERIODS}})
            if row["user"] not in entry["names"]:
                entry["names"].append(row["user"])
            for p in PERIODS:
                entry[p] = max(entry[p], athena.number(row[p]))
    return spend


def policy(blocked: list, disabled_models: list) -> dict:
    statements = [{
        "Sid": "OverBudget", "Effect": "Deny", "Action": INVOKE, "Resource": "*",
        "Condition": {"StringLike": {"aws:userid": [f"*:{user}" for user in blocked] or [NOBODY]}}}]
    models = sorted(m for m in disabled_models if MODEL.match(m))
    if models:
        # A cross-region profile (eu.anthropic.x, global.anthropic.x) and the model itself, for every region
        statements.append({"Sid": "ModelsTurnedOff", "Effect": "Deny", "Action": INVOKE, "Resource": [
            arn for m in models for arn in (f"arn:aws:bedrock:*:*:inference-profile/*.{m}",
                                            f"arn:aws:bedrock:*::foundation-model/{m}")]})
    return {"Version": "2012-10-17", "Statement": statements}


def apply(document: dict) -> bool:
    """A new default version of the policy when it changes. IAM keeps at most five versions."""
    current = iam.get_policy_version(PolicyArn=POLICY_ARN, VersionId=iam.get_policy(
        PolicyArn=POLICY_ARN)["Policy"]["DefaultVersionId"])["PolicyVersion"]["Document"]
    if current == document:
        return False
    versions = iam.list_policy_versions(PolicyArn=POLICY_ARN)["Versions"]
    if len(versions) >= 5:
        oldest = min((v for v in versions if not v["IsDefaultVersion"]), key=lambda v: v["CreateDate"])
        iam.delete_policy_version(PolicyArn=POLICY_ARN, VersionId=oldest["VersionId"])
    iam.create_policy_version(PolicyArn=POLICY_ARN, PolicyDocument=json.dumps(document), SetAsDefault=True)
    return True


def load(key: str):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def save(key: str, data):
    s3.put_object(Bucket=BUCKET, Key=key, ContentType="application/json", Body=json.dumps(data).encode())
