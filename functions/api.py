"""The dashboard's API. API Gateway has verified the sign-in token; this decides what the person may see.

People in an admin group see everyone and can filter to anyone; everyone else sees only themselves, whatever
they ask for. Their identity is the token's `USER_CLAIM`, which must equal their Identity Center user name: that
is the name telemetry and the bill carry.

  GET /api/me                  who you are, whether you're an admin, your budget status
  GET /api/usage               totals, spend over time and breakdowns, estimated (telemetry) and billed (CUR)
  GET /api/sessions            recent sessions
  GET /api/session?id=&from=&to=   one session's events, prompt by prompt (from/to: the days it ran)
  GET /api/budgets             (admin) the budgets and everyone's status
  PUT /api/budgets             (admin) replace the budgets
  PUT /api/reset               (admin) {"who": "<user>" or "*"}: forgive spend so far in the current periods
  GET /api/models              (admin) the Anthropic models Bedrock offers here, and which are turned off
  PUT /api/models              (admin) {"disabled": [model ids]}
  GET /api/install.sh          the installer, with this organisation's values in it
  GET /api/managed-settings.json   (admin) the same settings, for MDM
"""

import base64
import datetime
import json
import os
import re
import sys

import boto3

sys.path.insert(0, os.path.dirname(__file__))  # Lambda loads us as functions/<name>: our siblings
import athena  # noqa: E402
import client_files  # noqa: E402
import people  # noqa: E402

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
bedrock = boto3.client("bedrock")
lambda_ = boto3.client("lambda")
BUCKET = os.environ.get("BUCKET", "")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
url = None


def handler(event, context):
    me = person(event)
    if not me:
        return reply(403, {"message": "Your account isn't in a group that may use this dashboard."})
    route = event["rawPath"].removeprefix("/api")
    query = event.get("queryStringParameters") or {}
    method = event["requestContext"]["http"]["method"]
    try:
        if route == "/me":
            return reply(200, {**me, "budget": status().get("users", {}).get(me["user"].lower())})
        if route == "/usage":
            return reply(200, usage(me, query))
        if route == "/sessions":
            return reply(200, sessions(me, query))
        if route == "/session":
            return reply(200, session(me, query))
        if route == "/budgets" and me["admin"]:
            if method == "PUT":
                return reply(200, save_budgets(json.loads(event.get("body") or "{}")))
            return reply(200, {"budgets": budgets(), "status": status()})
        if route == "/reset" and me["admin"] and method == "PUT":
            return reply(200, reset(json.loads(event.get("body") or "{}").get("who", "")))
        if route == "/models" and me["admin"]:
            if method == "PUT":
                return reply(200, save_models(json.loads(event.get("body") or "{}")))
            return reply(200, models())
        if route == "/install.sh":
            return {"statusCode": 200, "headers": {"content-type": "text/x-shellscript",
                    "content-disposition": "attachment; filename=install-claude-code.sh"},
                    "body": client_files.install_script(client_values())}
        if route == "/managed-settings.json" and me["admin"]:
            return {"statusCode": 200, "headers": {"content-type": "application/json"},
                    "body": client_files.managed_settings(client_values())}
    except ValueError as error:
        return reply(400, {"message": str(error)})
    return reply(404, {"message": "Not found"})


# ---------------------------------------------------------------- who is asking

def claims(event) -> dict:
    """The token's own payload. API Gateway checked its signature, but flattens nested claims when it passes them
    on, and roles are often nested (Keycloak's resource_access.<client>.roles)."""
    token = ((event.get("headers") or {}).get("authorization") or "").removeprefix("Bearer ")
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def claim(data: dict, path: str):
    """A dot path into the claims, for example `resource_access.claude-code.roles`. A claim whose own name has dots
    (Auth0's https://example.com/roles) is matched whole first."""
    if path in data:
        return data[path]
    head, _, rest = path.partition(".")
    return claim(data[head], rest) if rest and isinstance(data.get(head), dict) else None


def person(event):
    data = claims(event)
    user = claim(data, os.environ["USER_CLAIM"])
    # People can often change their own email at the IdP: an unverified one could be a colleague's
    if os.environ["USER_CLAIM"] == "email" and data.get("email_verified") not in (True, "true"):
        return None
    values = claim(data, os.environ["ROLES_CLAIM"]) or []
    values = [values] if isinstance(values, str) else values
    admin = bool(set(values) & set(os.environ["ADMIN_VALUES"].split(",")))
    allowed = admin or bool(set(values) & set(os.environ["USER_VALUES"].split(",")))
    return {"user": user, "admin": admin} if user and allowed else None


# ---------------------------------------------------------------- usage

def scope(me: dict, query: dict):
    """The WHERE clause every telemetry query starts with, and its parameters: the date range, the person (for
    non-admins always themselves) and a tag, `tag=project=apollo`."""
    start, end = query.get("from", ""), query.get("to", "")
    if not (DAY.match(start) and DAY.match(end)) or start > end:
        raise ValueError("from and to are dates, YYYY-MM-DD, from first")
    if (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days > 400:
        raise ValueError("At most 400 days at a time")
    where, params = ["dt BETWEEN ? AND ?"], [start, end]
    user = me["user"] if not me["admin"] else query.get("user")
    if user:
        where.append('lower("user") = lower(?)')
        params.append(user)
    if query.get("tag") == UNTAGGED:
        where.append(NO_TAGS)
    elif query.get("tag"):
        key, _, value = query["tag"].partition("=")
        where.append("element_at(tags, ?) = ?")
        params.extend([key, value])
    return " AND ".join(where), params, user


def total(metric: str, *types: str) -> str:
    """SQL for the sum of one of Claude Code's metrics, optionally only some of its `type`s."""
    only = f" AND attrs['type'] IN ({', '.join(repr(t) for t in types)})" if types else ""
    return f"sum(if(name = 'claude_code.{metric}'{only}, value))"


COST, TOKENS = total("cost.usage"), total("token.usage")
UNTAGGED, NO_TAGS = "(none)", "coalesce(cardinality(tags), 0) = 0"
SESSIONS = "count(DISTINCT nullif(session, ''))"


def usage(me: dict, query: dict) -> dict:
    where, params, user = scope(me, query)
    billed_where, billed_params = billed_scope(query, user)
    results = athena.run({
        "totals": (f"""SELECT {COST} AS cost, {TOKENS} AS tokens,
                {total("token.usage", "cacheRead")} AS cache_read,
                {total("token.usage", "input", "cacheRead", "cacheCreation")} AS prompt_tokens,
                {total("lines_of_code.count", "added")} AS lines_added,
                {total("lines_of_code.count", "removed")} AS lines_removed,
                {total("commit.count")} AS commits, {total("pull_request.count")} AS pull_requests,
                {total("active_time.total")} AS active_seconds, count_if(name = 'user_prompt') AS prompts,
                count(DISTINCT "user") AS users, {SESSIONS} AS sessions
            FROM telemetry WHERE {where}""", params),
        "daily": (f"""SELECT dt AS day, model, sum(value) AS cost
            FROM telemetry WHERE {where} AND name = 'claude_code.cost.usage' GROUP BY 1, 2 ORDER BY 1""", params),
        "users": (f"""SELECT "user", {COST} AS cost, {TOKENS} AS tokens, {SESSIONS} AS sessions,
                count_if(name = 'user_prompt') AS prompts, {total("lines_of_code.count", "added")} AS lines_added,
                max(ts) AS last_seen
            FROM telemetry WHERE {where} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 500""", params),
        # A row per tag and value (a session tagged project=apollo and team=payments counts in both), and one for
        # usage with no tags at all
        "tags": (f"""SELECT * FROM (
                SELECT t.tag, t.tag_value, {COST} AS cost, count(DISTINCT "user") AS users, {SESSIONS} AS sessions
                FROM telemetry CROSS JOIN UNNEST(tags) AS t(tag, tag_value) WHERE {where} GROUP BY 1, 2
                UNION ALL
                SELECT '{UNTAGGED}', '', {COST}, count(DISTINCT "user"), {SESSIONS}
                FROM telemetry WHERE {where} AND {NO_TAGS} HAVING count(*) > 0)
            ORDER BY 3 DESC NULLS LAST LIMIT 300""", params + params),
        "models": (f"""SELECT model, {COST} AS cost, {TOKENS} AS tokens FROM telemetry
            WHERE {where} AND name IN ('claude_code.cost.usage', 'claude_code.token.usage') GROUP BY 1""", params),
        # main conversation, subagents, or Claude Code's own small calls
        "sources": (f"""SELECT coalesce(nullif(attrs['query_source'], ''), 'main') AS source, {COST} AS cost
            FROM telemetry WHERE {where} AND name = 'claude_code.cost.usage' GROUP BY 1 ORDER BY 2 DESC""", params),
        "tools": (f"""SELECT attrs['tool_name'] AS tool, count(*) AS calls, count_if(attrs['success'] = 'true') AS ok,
                avg(try_cast(attrs['duration_ms'] AS double)) AS avg_ms
            FROM telemetry WHERE {where} AND name = 'tool_result' GROUP BY 1 ORDER BY 2 DESC LIMIT 30""", params),
        "billed": (f"""SELECT CAST(date(line_item_usage_start_date) AS varchar) AS day,
                regexp_extract(line_item_iam_principal, '[^/]+$') AS "user", sum(line_item_unblended_cost) AS cost
            FROM cur WHERE {billed_where} GROUP BY 1, 2 ORDER BY 1""", billed_params),
    })

    # Model IDs differ between metrics and events and by region prefix: merge them under one name
    models = {}
    for row in results["models"]:
        name = model_name(row["model"])
        entry = models.setdefault(name, {"model": name, "cost": 0, "tokens": 0})
        entry["cost"] += athena.number(row["cost"])
        entry["tokens"] += athena.number(row["tokens"])
    daily = [{**numbers(r), "model": model_name(r["model"])} for r in results["daily"]]
    return {"totals": numbers(results["totals"][0]) if results["totals"] else {},
            "daily": daily,
            "users": [numbers(r) for r in results["users"]],
            "tags": [numbers(r) for r in results["tags"]],
            "models": sorted(models.values(), key=lambda m: -m["cost"]),
            "sources": [numbers(r) for r in results["sources"]],
            "tools": [numbers(r) for r in results["tools"]],
            "billed": [numbers(r) for r in results["billed"]]}


def billed_scope(query: dict, user):
    """CUR rows for the same days, from the Identity Center role only. Billing knows people, not tags."""
    start, end = query["from"], query["to"]
    roles = people.principal_patterns()
    where = ["billing_period BETWEEN ? AND ?", "line_item_product_code LIKE 'AmazonBedrock%'",
             "date(line_item_usage_start_date) BETWEEN date(?) AND date(?)",
             "(" + " OR ".join(["line_item_iam_principal LIKE ?"] * len(roles)) + ")"]
    params = [start[:7], end[:7], start, end, *roles]
    if user:
        where.append("lower(regexp_extract(line_item_iam_principal, '[^/]+$')) = lower(?)")
        params.append(user)
    return " AND ".join(where), params


def sessions(me: dict, query: dict) -> list:
    where, params, _ = scope(me, query)
    rows = athena.run({"sessions": (f"""SELECT session, "user", min(ts) AS started, max(ts) AS ended,
            json_format(CAST(max_by(tags, ts) AS JSON)) AS tags_json, {COST} AS cost,
            count_if(name = 'user_prompt') AS prompts, count_if(name = 'api_request') AS requests,
            count_if(name = 'tool_result') AS tools, max_by(model, value) AS model
        FROM telemetry WHERE {where} AND session <> ''
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 200""", params)})["sessions"]
    result = []
    for r in rows:
        tags = json.loads(r.pop("tags_json") or "{}")
        result.append({**numbers(r), "tags": tags, "model": model_name(r["model"] or "")})
    return result


EVENT_FIELDS = ["prompt.id", "prompt_length", "tool_name", "success", "duration_ms", "decision", "cost_usd",
                "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "query_source",
                "error", "status_code", "agent.name", "skill.name", "mcp_server.name"]


def session(me: dict, query: dict) -> list:
    """Only the days the session ran are read (the sessions list knows them), not all of history."""
    if not re.fullmatch(r"[0-9a-f-]{36}", query.get("id", "")):
        raise ValueError("id is a session id")
    where, params, _ = scope(me, {"from": query.get("from", ""), "to": query.get("to", "")})
    rows = athena.run({"events": (f"""SELECT ts, "user", name, model,
            json_format(CAST(attrs AS JSON)) AS attrs_json
        FROM telemetry
        WHERE {where} AND session = ? AND kind = 'event'
        ORDER BY ts, try_cast(attrs['event.sequence'] AS integer) LIMIT 5000""", [*params, query["id"]])})["events"]
    events = []
    for r in rows:
        attrs = json.loads(r["attrs_json"] or "{}")
        events.append({"ts": int(r["ts"]), "user": r["user"], "name": r["name"],
                       "model": model_name(r["model"] or ""), **{k: attrs[k] for k in EVENT_FIELDS if k in attrs}})
    return events


# ---------------------------------------------------------------- budgets

def budgets() -> dict:
    return load("config/budgets.json") or {"default": {}, "users": {}}


def status() -> dict:
    """What the enforcer found on its last run."""
    return load("state/budgets.json") or {}


def save_budgets(body: dict) -> dict:
    """{"default": {"daily": 20, "weekly": null, "monthly": 300}, "users": {"alice@example.com": {...}}} in USD."""
    def limits(entry):
        if not isinstance(entry, dict):
            raise ValueError("A budget is {daily, weekly, monthly} in USD")
        clean = {}
        for period in ("daily", "weekly", "monthly"):
            amount = entry.get(period)
            if amount not in (None, ""):
                if not isinstance(amount, (int, float)) or amount < 0:
                    raise ValueError(f"{period} must be a positive number of USD, or empty")
                clean[period] = amount
        return clean
    users = {name.strip().lower(): entry for name, entry in (body.get("users") or {}).items() if name.strip()}
    for name in users:
        if not people.NAME.match(name):
            raise ValueError(f"{name!r} isn't an Identity Center user name")
    clean = {"default": limits(body.get("default", {})), "users": {u: limits(v) for u, v in users.items()}}
    s3.put_object(Bucket=BUCKET, Key="config/budgets.json", Body=json.dumps(clean, indent=2).encode(),
                  ContentType="application/json")
    enforce()
    return clean


def reset(who: str) -> dict:
    """Waits for the enforcer, so the page shows the result: someone blocked can work again a moment later."""
    who = who.strip().lower()
    if who != "*" and not people.NAME.match(who):
        raise ValueError("who is a user name, or * for everyone")
    enforce({"reset": who}, wait=True)
    return status()


def enforce(event: dict = None, wait: bool = False):
    """Applies the controls now, instead of at the next scheduled check."""
    lambda_.invoke(FunctionName=os.environ["ENFORCER"], InvocationType="RequestResponse" if wait else "Event",
                   Payload=json.dumps(event or {}).encode())


# ---------------------------------------------------------------- models

def base_model(model_id: str) -> str:
    """eu.anthropic.claude-opus-4-6-v1[1m] -> anthropic.claude-opus-4-6-v1: one model, whichever regions it routes
    to, and whatever context window Claude Code asks for."""
    model_id = model_id.removesuffix("[1m]")
    return model_id[model_id.index("anthropic."):] if "anthropic." in model_id else model_id


def pinned() -> set:
    """The models the installer sets as Claude Code's defaults: turning one off would break everyone at once."""
    return {base_model(os.environ[f"MODEL_{tier}"]) for tier in ("OPUS", "SONNET", "HAIKU")}


def models() -> dict:
    available = {}
    for page in bedrock.get_paginator("list_inference_profiles").paginate(typeEquals="SYSTEM_DEFINED"):
        for profile in page["inferenceProfileSummaries"]:
            model = base_model(profile["inferenceProfileId"])
            if model.startswith("anthropic."):
                available.setdefault(model, []).append(profile["inferenceProfileId"])
    disabled = set((load("config/models.json") or {}).get("disabled", []))
    return {"models": sorted(({"id": m, "name": model_name(m), "profiles": sorted(p), "pinned": m in pinned(),
                               "disabled": m in disabled} for m, p in available.items()),
                             key=lambda m: m["name"])}


def save_models(body: dict) -> dict:
    disabled = sorted(set(body.get("disabled") or []))
    for model in disabled:
        if not re.fullmatch(r"anthropic\.[a-z0-9.:-]+", model):
            raise ValueError(f"{model!r} isn't an Anthropic model id")
        if model in pinned():
            raise ValueError(f"{model_name(model)} is one of Claude Code's defaults (config.yaml's models)")
    s3.put_object(Bucket=BUCKET, Key="config/models.json", Body=json.dumps({"disabled": disabled}).encode(),
                  ContentType="application/json")
    enforce()
    return models()


# ---------------------------------------------------------------- helpers

def load(key: str):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def client_values() -> dict:
    global url
    if url is None:
        url = ssm.get_parameter(Name=os.environ["URL_PARAMETER"])["Parameter"]["Value"]
    return client_files.values({**os.environ, "URL": url})


def model_name(model_id: str) -> str:
    """eu.anthropic.claude-opus-5-5, global.anthropic.claude-opus-5-5[1m] and claude-opus-5-5 are all `Opus 5.5`."""
    if not model_id:
        return ""
    name = re.sub(r"^(?:[a-z]+\.)?anthropic\.", "", model_id.split("/")[-1])
    name = re.sub(r"-v\d+(:\d+)?$|-\d{8}(-v\d+:\d+)?$|\[1m\]$", "", name).removeprefix("claude-")
    match = re.fullmatch(r"([a-z]+)-(\d+)(?:-(\d+))?", name)
    if match:
        family, major, minor = match.groups()
        return f"{family.title()} {major}.{minor}" if minor else f"{family.title()} {major}"
    return name


TEXT = {"user", "tag", "tag_value", "session", "model", "tool", "source", "day"}


def numbers(row: dict) -> dict:
    """Athena returns everything as text: the counts and sums become numbers."""
    return {key: value if key in TEXT else athena.number(value) for key, value in row.items()}


def reply(status_code: int, body) -> dict:
    return {"statusCode": status_code, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body)}
