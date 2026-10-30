"""Demo data for the README's screenshots, on a test deployment: eight people at example.com, 30 days of
workdays, a matching bill, a few budgets (one person blocked), some models off and required tags.

    uv run --with boto3 --with pyarrow python -I demo.py on  <stack name> <data bucket>
    uv run --with boto3 --with pyarrow python -I demo.py off <stack name> <data bucket>

`on` moves the deployment's own telemetry, bill and controls aside (under parked/), writes the demo, and runs the
enforcer. `off` deletes the demo, puts everything back, and runs the enforcer again. Never on a deployment people use.
"""

import datetime
import io
import json
import random
import sys
import uuid

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

mode, stack, bucket = sys.argv[1:4]
s3 = boto3.client("s3")
TODAY = datetime.datetime.now(datetime.UTC).date()
CONTROLS = {
    "config/budgets.json": {"default": {"daily": 40, "monthly": 400},
                            "users": {"erik.lund@example.com": {"monthly": 200},
                                      "tom.fisher@example.com": {"daily": 10}}},
    "config/models.json": {"disabled": ["anthropic.claude-fable-5-1", "anthropic.claude-fable-5",
                                        "anthropic.claude-opus-4-5-20251101-v1:0",
                                        "anthropic.claude-sonnet-4-5-20250929-v1:0"],
                           "default": "anthropic.claude-sonnet-5-5", "routes_off": []},
    "config/tags.json": {"tags": {"project": "require", "team": "warn"}},
}
PEOPLE = [  # name, sessions a day, how heavily, tags
    ("alice.berg", 3, 1.25, {"project": "apollo", "team": "payments"}),
    ("erik.lund", 2, 1.6, {"project": "apollo", "team": "payments"}),
    ("sara.nyman", 4, 0.9, {"project": "hermes", "team": "platform"}),
    ("maria.silva", 2, 1.0, {"project": "apollo", "team": "payments"}),
    ("lena.koch", 1, 1.3, {"project": "zeus", "team": "data"}),
    ("jonas.ek", 3, 0.5, {"project": "hermes", "team": "platform"}),
    ("omar.haddad", 2, 0.65, {"project": "zeus", "team": "data"}),
    ("tom.fisher", 1, 0.35, {}),
]
MODELS = [("eu.anthropic.claude-opus-5-5", 0.48, 5), ("eu.anthropic.claude-sonnet-5-5", 0.38, 3),
          ("eu.anthropic.claude-haiku-5-5", 0.14, 1)]  # model, share of calls, relative price
TOOLS = [("Read", 0.30, 13), ("Edit", 0.19, 10), ("Bash", 0.16, 900), ("Grep", 0.14, 40), ("Glob", 0.06, 21),
         ("Write", 0.05, 11), ("WebFetch", 0.03, 1800)]  # tool, share, milliseconds
SCHEMA = pa.schema([("ts", pa.int64()), ("user", pa.string()), ("tags", pa.map_(pa.string(), pa.string())),
                    ("kind", pa.string()), ("name", pa.string()), ("type", pa.string()), ("value", pa.float64()),
                    ("session", pa.string()), ("model", pa.string()), ("attrs", pa.map_(pa.string(), pa.string()))])


def keys(prefix: str) -> list:
    return [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
            for o in page.get("Contents", [])]


def move(key: str, to: str):
    s3.copy_object(Bucket=bucket, Key=to, CopySource={"Bucket": bucket, "Key": key})
    s3.delete_object(Bucket=bucket, Key=key)


def enforce():
    functions = boto3.client("cloudformation").describe_stack_resources(StackName=stack)["StackResources"]
    enforcer = next(r["PhysicalResourceId"] for r in functions
                    if r["ResourceType"] == "AWS::Lambda::Function" and r["LogicalResourceId"].startswith("Enforcer"))
    boto3.client("lambda").invoke(FunctionName=enforcer, Payload=b"{}")


def pick(options):
    r, total = random.random(), 0
    for option in options:
        total += option[1]
        if r <= total:
            return option
    return options[-1]


def day_of_usage(day: datetime.date, billed: dict) -> list:
    rows = []
    for name, per_day, weight, tags in PEOPLE:
        user = f"{name}@example.com"
        for _ in range(max(0, per_day + random.choice([-1, 0, 0, 1]))):
            session, seq = str(uuid.uuid4()), 0
            hour = random.randint(5, 6) if day == TODAY else random.randint(7, 15)  # today's are over already
            start = datetime.datetime.combine(day, datetime.time(hour, random.randint(0, 59)), datetime.UTC)
            minutes = random.randint(15, 60 if day == TODAY else 75)

            def add(t, kind, name, value=None, typ="", model="", user=user, tags=tags, session=session, **attrs):
                attrs = {"session.id": session, **({"type": typ} if typ else {}),
                         **({"model": model} if model else {}), **attrs}
                rows.append({"ts": int(t.timestamp() * 1000), "user": user, "tags": list(tags.items()), "kind": kind,
                             "name": name, "type": typ, "value": value, "session": session, "model": model,
                             "attrs": list(attrs.items())})

            add(start, "metric", "claude_code.session.count", 1.0, start_type="fresh")
            for minute in range(0, minutes, 2):
                t, prompt = start + datetime.timedelta(minutes=minute), str(uuid.uuid4())
                seq += 1
                add(t, "event", "user_prompt", **{"prompt.id": prompt, "event.sequence": str(seq),
                                                  "prompt_length": str(random.randint(20, 400))})
                for call in range(random.randint(1, 3)):
                    model, _, price = pick(MODELS)
                    source = "auxiliary" if "haiku" in model else random.choice(["main"] * 3 + ["subagent"])
                    tokens_in, tokens_out = random.randint(1, 40), random.randint(200, 2500)
                    cache_read, cache_new = random.randint(20000, 120000), random.randint(500, 6000)
                    cost = weight * price * 1.2e-6 * (tokens_out * 5 + cache_read * 0.1 + cache_new * 1.25 + tokens_in)
                    at = t + datetime.timedelta(seconds=5 + call * 9)
                    seq += 1
                    add(at, "event", "api_request", model=model.split(".", 1)[1].replace("anthropic.", ""),
                        **{"prompt.id": prompt, "event.sequence": str(seq), "cost_usd": f"{cost:.4f}",
                           "input_tokens": str(tokens_in), "output_tokens": str(tokens_out),
                           "cache_read_tokens": str(cache_read), "cache_creation_tokens": str(cache_new),
                           "duration_ms": str(random.randint(2000, 14000)), "query_source": source})
                    add(at, "metric", "claude_code.cost.usage", cost, model=model, query_source=source)
                    for typ, n in (("input", tokens_in), ("output", tokens_out), ("cacheRead", cache_read),
                                   ("cacheCreation", cache_new)):
                        add(at, "metric", "claude_code.token.usage", float(n), typ=typ, model=model,
                            query_source=source)
                    billed[(day, user)] = billed.get((day, user), 0) + cost * random.uniform(1.0, 1.08)
                    tool, _, ms = pick(TOOLS)
                    seq += 1
                    add(at, "event", "tool_decision", **{"prompt.id": prompt, "event.sequence": str(seq),
                                                         "tool_name": tool, "decision": "accept", "source": "config"})
                    seq += 1
                    add(at + datetime.timedelta(seconds=2), "event", "tool_result",
                        **{"prompt.id": prompt, "event.sequence": str(seq), "tool_name": tool,
                           "success": "true" if random.random() < 0.96 else "false",
                           "duration_ms": str(int(ms * random.uniform(0.5, 1.5)))})
                if random.random() < 0.5:
                    add(t, "metric", "claude_code.lines_of_code.count", float(random.randint(5, 90)), typ="added")
                    add(t, "metric", "claude_code.lines_of_code.count", float(random.randint(0, 35)), typ="removed")
                add(t, "metric", "claude_code.active_time.total", float(random.randint(40, 110)), typ="cli")
            end = start + datetime.timedelta(minutes=minutes)
            if random.random() < 0.8:
                add(end, "metric", "claude_code.commit.count", 1.0)
            if random.random() < 0.15:
                add(end, "metric", "claude_code.pull_request.count", 1.0)
    return rows


def write(key: str, rows: list, schema=None):
    out = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), out, compression="snappy")
    s3.put_object(Bucket=bucket, Key=key, Body=out.getvalue())


def on():
    if keys("parked/"):
        sys.exit("parked/ isn't empty: the demo is on already, or a run didn't finish. Run `off` first.")
    for key in keys("telemetry/") + keys("cur/") + keys("config/") + keys("state/forgiven.json"):
        move(key, f"parked/{key}")
    for key, value in CONTROLS.items():
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(value).encode(), ContentType="application/json")

    random.seed(7)
    billed = {}
    for back in range(30):
        day = TODAY - datetime.timedelta(days=back)
        if day.weekday() < 5:
            write(f"telemetry/dt={day.isoformat()}/demo-{day.isoformat()}.parquet", day_of_usage(day, billed), SCHEMA)
    # The bill: a line a day per person, a day behind
    export = f"{stack}-bedrock-by-principal"
    lines = [{"line_item_usage_start_date": datetime.datetime.combine(day, datetime.time(0)),
              "line_item_product_code": "AmazonBedrockService", "line_item_usage_type": "EUN1-Claude-tokens",
              "line_item_iam_principal":
                  f"arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_ClaudeCode_0123456789abcdef/{user}",
              "line_item_usage_amount": 1.0, "line_item_unblended_cost": cost}
             for (day, user), cost in billed.items() if day < TODAY]
    for period in sorted({line["line_item_usage_start_date"].strftime("%Y-%m") for line in lines}):
        write(f"cur/{export}/data/BILLING_PERIOD={period}/demo-{period}.parquet",
              [line for line in lines if line["line_item_usage_start_date"].strftime("%Y-%m") == period])
    enforce()
    print("Demo on. Take the screenshots, then run `off`.")


def off():
    if not keys("parked/"):
        sys.exit("parked/ is empty: the demo isn't on, and there's nothing to put back.")
    for key in keys("telemetry/") + keys("cur/") + keys("config/") + keys("state/forgiven.json"):
        if "/demo-" in key or not key.startswith(("telemetry/", "cur/")):
            s3.delete_object(Bucket=bucket, Key=key)
    for key in keys("parked/"):
        move(key, key.removeprefix("parked/"))
    enforce()
    print("Demo off: the deployment's own data and controls are back.")


{"on": on, "off": off}[mode]()
