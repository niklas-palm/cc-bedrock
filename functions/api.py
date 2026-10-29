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
  PUT /api/models              (admin) {"disabled": [model ids], "default": model id, "routes_off": ["global"]}
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
import models  # noqa: E402
import people  # noqa: E402

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
bedrock = boto3.client("bedrock")
lambda_ = boto3.client("lambda")
BUCKET = os.environ.get("BUCKET", "")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
url = None


class Unavailable(Exception):
    """Something that didn't work this time, said to the page as it is."""


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
                return reply(200, save_model_list(json.loads(event.get("body") or "{}")))
            return reply(200, model_list())
        if route == "/install.sh":
            return {"statusCode": 200, "headers": {"content-type": "text/x-shellscript",
                    "content-disposition": "attachment; filename=install-claude-code.sh"},
                    "body": client_files.install_script(client_values(), picks())}
        if route == "/managed-settings.json" and me["admin"]:
            return {"statusCode": 200, "headers": {"content-type": "application/json"},
                    "body": client_files.managed_settings(client_values(), picks())}
    except ValueError as error:
        return reply(400, {"message": str(error)})
    except (TimeoutError, Unavailable) as error:
        return reply(503, {"message": str(error)})
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
    if not (DAY.fullmatch(start) and DAY.fullmatch(end)) or start > end:
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
    only = f" AND type IN ({', '.join(repr(t) for t in types)})" if types else ""
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
                count_if(name = 'user_prompt') AS prompts, max(ts) AS last_seen
            FROM telemetry WHERE {where} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 5000""", params),
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
        "billed": (f"""SELECT {people.BILLED_USER} AS "user", sum(line_item_unblended_cost) AS cost
            FROM cur WHERE {billed_where} GROUP BY 1""", billed_params),
    })

    # Model IDs differ between metrics and events and by region prefix: merge them under one name
    by_model = {}
    for row in results["models"]:
        name = models.model_name(row["model"])
        entry = by_model.setdefault(name, {"model": name, "cost": 0, "tokens": 0})
        entry["cost"] += athena.number(row["cost"])
        entry["tokens"] += athena.number(row["tokens"])
    daily = [{**numbers(r), "model": models.model_name(r["model"])} for r in results["daily"]]
    return {"totals": numbers(results["totals"][0]) if results["totals"] else {},
            "daily": daily,
            "users": [numbers(r) for r in results["users"]],
            "tags": [numbers(r) for r in results["tags"]],
            "models": sorted(by_model.values(), key=lambda m: -m["cost"]),
            "sources": [numbers(r) for r in results["sources"]],
            "tools": [numbers(r) for r in results["tools"]],
            "billed": [numbers(r) for r in results["billed"]]}


def billed_scope(query: dict, user):
    """CUR rows for the same days, from the Identity Center role only. Billing knows people, not tags."""
    start, end = query["from"], query["to"]
    lines, params = people.billed_lines(start[:7], end[:7])
    where = [lines, "date(line_item_usage_start_date) BETWEEN date(?) AND date(?)"]
    params += [start, end]
    if user:
        where.append(f"lower({people.BILLED_USER}) = lower(?)")
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
        result.append({**numbers(r), "tags": tags, "model": models.model_name(r["model"] or "")})
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
                       "model": models.model_name(r["model"] or ""),
                       **{k: attrs[k] for k in EVENT_FIELDS if k in attrs}})
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
        if not people.NAME.fullmatch(name):
            raise ValueError(f"{name!r} isn't an Identity Center user name")
    clean = {"default": limits(body.get("default", {})), "users": {u: limits(v) for u, v in users.items()}}
    s3.put_object(Bucket=BUCKET, Key="config/budgets.json", Body=json.dumps(clean, indent=2).encode(),
                  ContentType="application/json")
    enforce()
    return clean


def reset(who: str) -> dict:
    """Waits for the enforcer, so the page shows the result: someone blocked can work again a moment later."""
    who = who.strip().lower()
    if who != "*" and not people.NAME.fullmatch(who):
        raise ValueError("who is a user name, or * for everyone")
    enforce({"reset": who}, wait=True)
    return status()


def enforce(event: dict = None, wait: bool = False):
    """Applies the controls now, instead of at the next scheduled check. A reset waits for it, to show the result."""
    response = lambda_.invoke(FunctionName=os.environ["ENFORCER"], Payload=json.dumps(event or {}).encode(),
                              InvocationType="RequestResponse" if wait else "Event")
    if response.get("FunctionError"):
        raise Unavailable("The budget check failed: see the enforcer's logs. A reset it saved applies next time")


# ---------------------------------------------------------------- models

def pinned() -> dict:
    """config.yaml's models, {model: (tier, ID)}: what Claude Code uses for each tier (its background calls are
    Haiku, for one), so they stay on."""
    configured = {tier: os.environ[f"MODEL_{tier.upper()}"] for tier in ("opus", "sonnet", "haiku")}
    return models.pinned(configured, bedrock)


def model_settings() -> dict:
    return load("config/models.json") or {}


def model_list() -> dict:
    """Every Anthropic model Bedrock offers here, whether it's allowed, which one new sessions start on, and the
    routings its inference profiles take (eu., global.) with which of those are allowed."""
    settings, tiers = model_settings(), pinned()
    available, off = models.available(bedrock), settings.get("routes_off", [])
    routes = sorted({models.route(p) for profiles in available.values() for p in profiles},
                    key=lambda r: (r == "global", r))
    return {
        "default": settings.get("default", ""),
        "routes": [{"id": r, "name": models.ROUTES.get(r, r), "off": r in off, "pinned": r in pinned_routes(tiers)}
                   for r in routes],
        "models": [{"id": m, "name": models.model_name(m), "profiles": sorted(p), "tier": tiers.get(m, (None,))[0],
                    "disabled": m in settings.get("disabled", []),
                    "routable": any(models.route(profile) not in off for profile in p)}
                   for m, p in sorted(available.items(), key=lambda item: models.order(item[0]))]}


def pinned_routes(tiers: dict) -> set:
    """The routings config.yaml's models go through (an application profile's ARN doesn't go through any)."""
    return {models.route(model_id) for _, model_id in tiers.values() if not model_id.startswith("arn:")}


def save_model_list(body: dict) -> dict:
    """{"disabled": [model ids], "default": model id or "", "routes_off": ["global", ...]}."""
    disabled, default = sorted(set(body.get("disabled") or [])), body.get("default") or ""
    routes_off = sorted(set(body.get("routes_off") or []))
    for model in disabled + ([default] if default else []):
        if not models.MODEL_ID.fullmatch(model):
            raise ValueError(f"{model!r} isn't an Anthropic model id")
    for route in routes_off:
        if not models.ROUTE_ID.fullmatch(route):
            raise ValueError(f"{route!r} isn't a routing")
    tiers = pinned()
    for model, (tier, _) in tiers.items():
        if model in disabled:
            raise ValueError(f"Claude Code uses {models.model_name(model)} as its {tier} (config.yaml): it stays on")
    for route in set(routes_off) & pinned_routes(tiers):
        raise ValueError(f"Claude Code's own models (config.yaml) go through {models.ROUTES.get(route, route)}: "
                         "that stays on")
    if default in disabled:
        raise ValueError("The default model must be one that's allowed")
    available = models.available(bedrock)
    if default and default not in tiers and not any(models.route(p) not in routes_off
                                                    for p in available.get(default, [])):
        raise ValueError(f"{models.model_name(default)} has no inference profile in the regions allowed")
    s3.put_object(Bucket=BUCKET, Key="config/models.json", ContentType="application/json",
                  Body=json.dumps({"disabled": disabled, "default": default, "routes_off": routes_off}).encode())
    enforce()
    return model_list()


def picks() -> dict:
    """The installer's /model choices and default, from what the admins allow."""
    return models.choices(models.available(bedrock), model_settings(), pinned())


# ---------------------------------------------------------------- helpers

def load(key: str):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def client_values() -> dict:
    """The installer's values: the stack put them in this function's environment, all but the dashboard's URL,
    which it can't know until CloudFront exists."""
    global url
    if url is None:
        url = ssm.get_parameter(Name=os.environ["URL_PARAMETER"])["Parameter"]["Value"]
    return {**{key: os.environ[key] for key in client_files.KEYS if key != "URL"}, "URL": url}


TEXT = {"user", "tag", "tag_value", "session", "model", "tool", "source", "day"}


def numbers(row: dict) -> dict:
    """Athena returns everything as text: the counts and sums become numbers."""
    return {key: value if key in TEXT else athena.number(value) for key, value in row.items()}


def reply(status_code: int, body) -> dict:
    return {"statusCode": status_code, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body)}
