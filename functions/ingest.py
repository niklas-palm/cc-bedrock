"""Receives Claude Code's OpenTelemetry (OTLP/HTTP, JSON) and writes one row per data point or event to Firehose.

Who sent it is proven, not claimed: the otel-helper on the laptop presigns an STS GetCallerIdentity request with
the person's own Identity Center credentials and sends the URL in `x-cc-identity`. Calling that URL tells us the
caller's role and session name, which for Identity Center is the person's user name: the same identity Bedrock
records on the bill. No shared secret to distribute, and nobody without the Bedrock role can send telemetry.
"""

import base64
import calendar
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import boto3

sys.path.insert(0, os.path.dirname(__file__))  # Lambda loads us as functions/<name>: our siblings
import people  # noqa: E402

firehose = boto3.client("firehose")
TAG_KEY = re.compile(r"^[a-z0-9_]{1,32}$")
TAG_VALUE = re.compile(r"^[\w .:/@+=-]{1,100}$")
MAX_TAGS = 20
verified = {}  # presigned URL -> (user, valid until): a warm function checks each URL with STS once


def handler(event, context):
    headers = event.get("headers") or {}
    user = identity(headers.get("x-cc-identity", ""))
    if not user:
        return reply(401, {"message": "x-cc-identity is missing, expired or not a Claude Code role"})
    try:
        body = base64.b64decode(event["body"]) if event.get("isBase64Encoded") else (event.get("body") or "").encode()
        if headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body)
    except (ValueError, OSError):
        return reply(415, {"message": "send OTLP as JSON: OTEL_EXPORTER_OTLP_PROTOCOL=http/json"})

    rows = flatten(payload, user, parse_tags(headers.get("x-cc-tags", "")))
    put(rows)
    return reply(200, {})


def identity(url: str):
    """The Identity Center user name behind a presigned GetCallerIdentity URL, or None."""
    now = time.time()
    if url in verified and verified[url][1] > now:
        return verified[url][0]
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    # Only ever call STS: anything else would let a sender make this function fetch arbitrary URLs
    if parts.scheme != "https" or not re.fullmatch(r"sts(\.[a-z0-9-]+)?\.amazonaws\.com", parts.netloc or "") \
            or query.get("Action") != ["GetCallerIdentity"]:
        return None
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            arn = json.load(response)["GetCallerIdentityResponse"]["GetCallerIdentityResult"]["Arn"]
        # Trusted for at most an hour from signing, whatever expiry the URL claims, so revoked access stops soon
        until = calendar.timegm(time.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ")) + min(
            int(query.get("X-Amz-Expires", ["900"])[0]), 3600)
    except (OSError, ValueError, KeyError):
        return None
    user = user_from_arn(arn)
    if user:
        if len(verified) > 10_000:  # forget the expired ones
            for key in [k for k, (_, valid_until) in verified.items() if valid_until <= now]:
                del verified[key]
        verified[url] = (user, until)
    return user


def user_from_arn(arn: str):
    """arn:aws:sts::<this account>:assumed-role/<allowed role>/<session> -> session (the user name)."""
    match = re.fullmatch(r"arn:aws:sts::(\d{12}):assumed-role/([^/]+)/(.+)", arn)
    if not match or match.group(1) != os.environ["ACCOUNT"]:
        return None
    if not people.is_person_role(match.group(2)):
        return None
    return match.group(3)


def value(v: dict) -> str:
    """An OTLP AnyValue as text."""
    if not v:
        return ""
    kind, content = next(iter(v.items()))
    if kind in ("arrayValue", "kvlistValue"):
        return json.dumps(content)
    return str(content).lower() if kind == "boolValue" else str(content)


def attributes(items: list) -> dict:
    return {a["key"]: value(a.get("value")) for a in items or []}


def parse_tags(header: str) -> dict:
    """The sender's CC_* tags, {"project": "apollo"}: whatever is well-formed, the rest dropped."""
    try:
        sent = json.loads(header or "{}")
    except ValueError:
        return {}
    if not isinstance(sent, dict):
        return {}
    good = {k: v for k, v in sent.items() if isinstance(v, str) and TAG_KEY.match(k) and TAG_VALUE.match(v)}
    return dict(sorted(good.items())[:MAX_TAGS])


def flatten(payload: dict, user: str, tags: dict) -> list:
    rows = []

    def row(ts_nano, kind, name, number, attrs):
        rows.append({"ts": int(ts_nano or time.time_ns()) // 1_000_000, "user": user, "tags": tags,
                     "kind": kind, "name": name, "value": number, "session": attrs.get("session.id", ""),
                     "model": attrs.get("model", ""), "attrs": attrs})

    for resource in payload.get("resourceMetrics", []):
        base = attributes(resource.get("resource", {}).get("attributes"))
        for scope in resource.get("scopeMetrics", []):
            for metric in scope.get("metrics", []):
                for kind in ("sum", "gauge"):
                    for point in metric.get(kind, {}).get("dataPoints", []):
                        number = float(point.get("asDouble", point.get("asInt", 0)))
                        row(point.get("timeUnixNano"), "metric", metric["name"], number,
                            {**base, **attributes(point.get("attributes"))})

    for resource in payload.get("resourceLogs", []):
        base = attributes(resource.get("resource", {}).get("attributes"))
        for scope in resource.get("scopeLogs", []):
            for record in scope.get("logRecords", []):
                attrs = {**base, **attributes(record.get("attributes"))}
                name = attrs.get("event.name") or value(record.get("body")).removeprefix("claude_code.")
                row(record.get("timeUnixNano"), "event", name, None, attrs)
    return rows


def put(rows: list):
    """Firehose takes 500 records or 4 MB a call; retry what it throttles once."""
    for start in range(0, len(rows), 400):
        records = [{"Data": (json.dumps(r, separators=(",", ":")) + "\n").encode()} for r in rows[start:start + 400]]
        result = firehose.put_record_batch(DeliveryStreamName=os.environ["STREAM"], Records=records)
        if result["FailedPutCount"]:
            retry = [rec for rec, res in zip(records, result["RequestResponses"], strict=True) if "ErrorCode" in res]
            firehose.put_record_batch(DeliveryStreamName=os.environ["STREAM"], Records=retry)


def reply(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}
