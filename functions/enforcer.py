"""Applies the admins' controls to Bedrock, through two IAM policies that the Identity Center permission set
includes: whatever they deny, Bedrock refuses at once, from any client, naming the policy, so people see why.

  <name>-over-budget  people over a daily, weekly or monthly limit, until the period ends, an admin raises the
                      limit, or an admin resets them (their spend so far in the current periods is forgiven)
  <name>-models-off   models the admins turned off, and routings (global., say) they don't allow, for everyone

Runs every 10 minutes, and at once when an admin changes something ({"reset": "<user>" or "*"} to reset).
Spend is the larger of two figures: Claude Code's own estimate from telemetry (minutes behind, list prices)
and the bill (CUR 2.0 by IAM principal: exact, but up to a day behind), so missing telemetry can't escape a budget
for long. Periods are UTC; weeks start on Monday. The deny matches `aws:userid`, which for an Identity Center
session is `<role id>:<user name>`.
"""

import datetime
import json
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(__file__))  # Lambda loads us as functions/<name>: our siblings
import athena  # noqa: E402
import models  # noqa: E402
import people  # noqa: E402

iam = boto3.client("iam")
s3 = boto3.client("s3")
BUCKET = os.environ.get("BUCKET", "")
OVER_BUDGET_POLICY = os.environ.get("OVER_BUDGET_POLICY", "")
MODELS_OFF_POLICY = os.environ.get("MODELS_OFF_POLICY", "")
PERIODS = ("daily", "weekly", "monthly")
NOBODY, NO_MODEL = "nobody-is-over-budget", "arn:aws:bedrock:*::foundation-model/no-model-is-off"
INVOKE = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]


def handler(event, context):
    budgets = load("config/budgets.json") or {"default": {}, "users": {}}
    allowed = load("config/models.json") or {}
    today = datetime.datetime.now(datetime.UTC).date()
    starts = {"daily": today, "weekly": today - datetime.timedelta(days=today.weekday()),
              "monthly": today.replace(day=1)}

    # Models first: they need no spend, so nothing below can hold them back
    apply(MODELS_OFF_POLICY, models_off(allowed.get("disabled", []), allowed.get("routes_off", [])))

    spend = spending(starts, today)
    # Read after the queries, the moment before it's used: two resets seconds apart would otherwise each save
    # only their own, and the first person would stay blocked
    forgiven = load("state/forgiven.json") or {}
    if event.get("reset"):
        forgiven = reset(forgiven, spend, starts, event["reset"])
        save("state/forgiven.json", forgiven)
    users, blocked = assess(budgets, forgive(spend, forgiven, starts))
    document, left_out = fit(blocked)
    apply(OVER_BUDGET_POLICY, document)
    save("state/budgets.json", {"updated": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                                "blocked": [name for name in blocked if name not in left_out],
                                "not_blocked": left_out, "users": users})
    return {"blocked": blocked, "not_blocked": left_out}


def reset(forgiven: dict, spend: dict, starts: dict, who: str) -> dict:
    """Forgive what someone (or "*", everyone) has spent so far in the current periods: their budgets start again
    from now, as they would when a period ends. A forgiveness lasts until its period ends.

    The bill is a day or so behind, so what it says now isn't all of the spend before the reset: the estimate so far
    is forgiven, and the bill only counts again from the day after the reset."""
    for key in spend if who == "*" else [who.lower()]:
        if key in spend:
            forgiven[key] = {p: [starts[p].isoformat(), spend[key]["estimated"][p]] for p in PERIODS}
            forgiven[key]["day"] = starts["daily"].isoformat()
    return forgiven


def forgive(spend: dict, forgiven: dict, starts: dict) -> dict:
    """Spend with what was forgiven in the current periods taken off."""
    result = {}
    for key, entry in spend.items():
        result[key] = dict(entry)
        given = forgiven.get(key, {})
        for p in PERIODS:
            period, amount = given.get(p, [None, 0])
            if period != starts[p].isoformat():
                continue
            billed = sum(cost for day, cost in entry["billed_days"].items()
                         if day > given["day"] and day >= starts[p].isoformat())
            result[key][p] = max(entry["estimated"][p] - amount, billed, 0.0)
    return result


def assess(budgets: dict, spend: dict):
    """Everyone's spend against their limits ({user: status}), and the session names to deny.

    People are keyed by their lower-cased name; the deny lists every spelling seen, since IAM compares
    case-sensitively, or before any is, the one their budget was typed with. So a budget on someone who hasn't spent
    yet can block them too (a limit of 0), typed as Identity Center spells them."""
    typed = {name.lower(): (name, limits) for name, limits in budgets.get("users", {}).items()}
    users = {}
    for key in set(spend) | set(typed):
        name, own = typed.get(key, (None, {}))
        limits = {**budgets.get("default", {}), **own}
        entry = spend.get(key) or {"names": [], **{p: 0.0 for p in PERIODS}}
        names = entry["names"] or [name]
        spent = {p: entry[p] for p in PERIODS}
        over = [p for p in PERIODS if limits.get(p) is not None and spent[p] >= limits[p]]
        users[key] = {"user": names[0], "names": names, "spent": spent, "limits": limits, "over": over}
    def overspend(u):
        return max(u["spent"][p] - u["limits"][p] for p in u["over"])

    over = sorted((u for u in users.values() if u["over"]), key=lambda u: (-overspend(u), u["user"]))
    blocked = [name for u in over for name in u["names"] if people.NAME.fullmatch(name)]
    return users, blocked  # those furthest over first


def fit(blocked: list):
    """The over-budget policy, and who didn't fit in it: IAM allows 6,144 characters, about 150 people. The ones
    furthest over are kept."""
    for keep in range(len(blocked), -1, -1):
        document = over_budget(sorted(blocked[:keep]))  # sorted: a new order alone doesn't rewrite the policy
        if len(json.dumps(document, separators=(",", ":"))) <= 6144:
            if keep < len(blocked):
                print(json.dumps({"policy_full": blocked[keep:]}))
            return document, blocked[keep:]


def spending(starts: dict, today: datetime.date) -> dict:
    """{lower-cased user: {names, daily, weekly, monthly}}, each the larger of the estimate and the bill; with the
    parts kept (`estimated` per period, `billed_days`) for what a reset forgives."""
    first = min(starts.values())
    sums = ", ".join(f"sum(if(dt >= '{starts[p].isoformat()}', value)) AS {p}" for p in PERIODS)
    lines, params = people.billed_lines(first.isoformat()[:7], today.isoformat()[:7])
    results = athena.run(seconds=100, queries={
        "estimated": (f"""SELECT "user", {sums} FROM telemetry
                WHERE dt BETWEEN ? AND ? AND name = 'claude_code.cost.usage' GROUP BY 1""",
            [first.isoformat(), today.isoformat()]),
        "billed": (f"""SELECT {people.BILLED_USER} AS "user",
                CAST(date(line_item_usage_start_date) AS varchar) AS day, sum(line_item_unblended_cost) AS cost
            FROM cur WHERE {lines} AND date(line_item_usage_start_date) >= date(?) GROUP BY 1, 2""",
            [*params, first.isoformat()]),
    })
    spend = {}

    def entry(user):
        found = spend.setdefault(user.lower(), {"names": [], "estimated": dict.fromkeys(PERIODS, 0.0),
                                                "billed_days": {}})
        if user not in found["names"]:
            found["names"].append(user)
        return found

    for row in results["estimated"]:
        entry(row["user"])["estimated"] = {p: athena.number(row[p]) for p in PERIODS}
    for row in results["billed"]:
        days = entry(row["user"])["billed_days"]
        days[row["day"]] = days.get(row["day"], 0.0) + athena.number(row["cost"])
    for found in spend.values():
        for p in PERIODS:
            billed = sum(cost for day, cost in found["billed_days"].items() if day >= starts[p].isoformat())
            found[p] = max(found["estimated"][p], billed)
    return spend


def over_budget(blocked: list) -> dict:
    return {"Version": "2012-10-17", "Statement": [{
        "Sid": "OverBudget", "Effect": "Deny", "Action": INVOKE, "Resource": "*",
        "Condition": {"StringLike": {"aws:userid": [f"*:{user}" for user in blocked] or [NOBODY]}}}]}


def models_off(disabled: list, routes_off: list = ()) -> dict:
    """A model turned off: its cross-region profiles (eu.anthropic.x, global.anthropic.x) and the model itself, in
    every region. A routing turned off: every profile that routes that way (global.*)."""
    off = sorted(m for m in disabled if models.MODEL_ID.fullmatch(m))
    resources = [arn for m in off for arn in (f"arn:aws:bedrock:*:*:inference-profile/*.{m}",
                                                  f"arn:aws:bedrock:*::foundation-model/{m}")]
    resources += [f"arn:aws:bedrock:*:*:inference-profile/{r}.*"
                  for r in sorted(routes_off) if models.ROUTE_ID.fullmatch(r)]
    return {"Version": "2012-10-17", "Statement": [{
        "Sid": "ModelsTurnedOff", "Effect": "Deny", "Action": INVOKE, "Resource": resources or [NO_MODEL]}]}


def apply(policy_arn: str, document: dict):
    """A new default version of the policy when it changes. IAM keeps at most five versions, so the oldest goes.

    Two runs can overlap (the schedule and an admin's change): one may delete the version the other meant to, or
    fill the fifth slot first. Either way the second tries once more; the next run settles anything left."""
    current = iam.get_policy_version(PolicyArn=policy_arn, VersionId=iam.get_policy(
        PolicyArn=policy_arn)["Policy"]["DefaultVersionId"])["PolicyVersion"]["Document"]
    if current == document:
        return
    for attempt in range(2):
        versions = iam.list_policy_versions(PolicyArn=policy_arn)["Versions"]
        if len(versions) >= 5:
            oldest = min((v for v in versions if not v["IsDefaultVersion"]), key=lambda v: v["CreateDate"])
            try:
                iam.delete_policy_version(PolicyArn=policy_arn, VersionId=oldest["VersionId"])
            except iam.exceptions.NoSuchEntityException:
                pass  # the other run deleted it
        try:
            iam.create_policy_version(PolicyArn=policy_arn, PolicyDocument=json.dumps(document), SetAsDefault=True)
            return
        except iam.exceptions.LimitExceededException:
            if attempt:
                raise


def load(key: str):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def save(key: str, data):
    s3.put_object(Bucket=BUCKET, Key=key, ContentType="application/json", Body=json.dumps(data).encode())
