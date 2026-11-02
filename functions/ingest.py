"""Receives Claude Code's OpenTelemetry (OTLP/HTTP, JSON) and writes one row per data point or event to Firehose.

Who sent it is proven, not claimed: the otel-helper on the laptop presigns an STS GetCallerIdentity request with
the person's own Identity Center credentials and sends the URL in `x-cc-identity`. Calling that URL tells us the
caller's role and session name, which for Identity Center is the person's user name: the same identity Bedrock
records on the bill. No shared secret to distribute, and nobody without the Bedrock role can send telemetry.

With the same identity, GET /v1/tags tells Claude Code's hook which tags the admins require.
"""

import base64
import calendar
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zlib

import boto3
import botocore.config
import botocore.exceptions

sys.path.insert(0, os.path.dirname(__file__))  # Lambda loads us as functions/<name>: our siblings
import people  # noqa: E402

# Few retries and short waits of its own: put() retries, and the whole request must fit in the function's 10 s
firehose = boto3.client("firehose", config=botocore.config.Config(
    retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=3))
s3 = boto3.client("s3")
TAG_KEY = re.compile(r"[a-z0-9_]{1,32}")
TAG_VALUE = re.compile(r"[\w .:/@+=-]{1,100}")
MAX_TAGS = 20
MAX_BODY = 20 * 1024 * 1024  # unzipped; API Gateway takes 10 MB, zipped
verified = {}  # presigned URL -> (user, valid until): a warm function checks each URL with STS once


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an error: only STS's own answer counts


sts = urllib.request.build_opener(NoRedirects)


def handler(event, context):
    headers = event.get("headers") or {}
    user = identity(headers.get("x-cc-identity", ""))
    if not user:
        return reply(401, {"message": "x-cc-identity is missing, expired or not a Claude Code role"})
    if event.get("rawPath") == "/v1/tags":
        return reply(200, required_tags())
    try:
        body = base64.b64decode(event["body"]) if event.get("isBase64Encoded") else (event.get("body") or "").encode()
        if headers.get("content-encoding") == "gzip":
            unzip = zlib.decompressobj(wbits=31)
            body = unzip.decompress(body, MAX_BODY)  # a few MB can unpack to gigabytes
            if unzip.unconsumed_tail:
                return reply(413, {"message": "that's more than a batch of telemetry"})
            if not unzip.eof:
                raise ValueError("gzip cut short")
        payload = json.loads(body)
    except (ValueError, OSError, zlib.error):
        return reply(415, {"message": "send OTLP as JSON: OTEL_EXPORTER_OTLP_PROTOCOL=http/json"})
    try:
        rows = flatten(payload, user, parse_tags(headers.get("x-cc-tags", "")))
    except (AttributeError, KeyError, TypeError, ValueError):
        return reply(400, {"message": "that isn't OTLP"})
    lost = put(rows)
    if lost and lost == len(rows):
        return reply(503, {"message": "Couldn't store it: send it again"})  # Claude Code sends it again
    if lost:  # sending it all again would store the rest twice
        print(json.dumps({"lost": lost, "of": len(rows), "user": user}))
    return reply(200, {})


def required_tags() -> dict:
    """What the admins set on the dashboard's Controls tab: {"tags": {"project": "require", "team": "warn"}}."""
    try:
        return json.loads(s3.get_object(Bucket=os.environ["BUCKET"], Key="config/tags.json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {"tags": {}}


def identity(url: str):
    """The Identity Center user name behind a presigned GetCallerIdentity URL, or None."""
    now = time.time()
    if url in verified and verified[url][1] > now:
        return verified[url][0]
    try:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
    except ValueError:
        parts, query = None, {}
    # Only ever call STS itself, by its exact names: a looser pattern also matches S3 buckets called "sts"
    # (sts.s3.amazonaws.com), whose owner could then vouch for anyone
    hosts = {"sts.amazonaws.com", f"sts.{os.environ['AWS_REGION']}.amazonaws.com"}
    if not parts or parts.scheme != "https" or parts.netloc not in hosts \
            or query.get("Action") != ["GetCallerIdentity"]:
        print(json.dumps({"refused": "no presigned STS URL" if url else "no x-cc-identity header"}))
        return None
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with sts.open(request, timeout=5) as response:
            arn = json.load(response)["GetCallerIdentityResponse"]["GetCallerIdentityResult"]["Arn"]
        # Trusted for at most an hour from signing, whatever expiry the URL claims, so revoked access stops soon
        until = calendar.timegm(time.strptime(query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ")) + min(
            int(query.get("X-Amz-Expires", ["900"])[0]), 3600)
    except (OSError, ValueError, KeyError, http.client.HTTPException) as error:
        print(json.dumps({"refused": f"STS didn't confirm it (expired credentials?): {type(error).__name__}"}))
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
    if not match or match.group(1) != os.environ["ACCOUNT"] or not people.is_person_role(match.group(2)):
        print(json.dumps({"refused": "not this account's Claude Code role (see telemetry_roles)", "arn": arn}))
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
    good = {k: v for k, v in sent.items() if isinstance(v, str) and TAG_KEY.fullmatch(k) and TAG_VALUE.fullmatch(v)}
    return dict(sorted(good.items())[:MAX_TAGS])


def flatten(payload: dict, user: str, tags: dict) -> list:
    rows = []

    def row(ts_nano, kind, name, number, attrs):
        rows.append({"ts": int(ts_nano or time.time_ns()) // 1_000_000, "user": user, "tags": tags,
                     "kind": kind, "name": name, "type": attrs.get("type", ""), "value": number,
                     "session": attrs.get("session.id", ""), "model": attrs.get("model", ""), "attrs": attrs})

    for resource in payload.get("resourceMetrics", []):
        base = attributes(resource.get("resource", {}).get("attributes"))
        for scope in resource.get("scopeMetrics", []):
            for metric in scope.get("metrics", []):
                for kind in ("sum", "gauge"):
                    for point in metric.get(kind, {}).get("dataPoints", []):
                        number = float(point.get("asDouble", point.get("asInt", 0)))
                        # Every metric Claude Code sends is a count or an amount: a negative one would lower an
                        # estimate, and so a budget's spend
                        if not math.isfinite(number) or number < 0:
                            continue
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


def put(rows: list) -> int:
    """Firehose takes 500 records or 4 MB a call. What it refuses (throttling, mostly) is tried again a little
    later, twice. Returns how many it still refused."""
    failed = 0
    for start in range(0, len(rows), 400):
        records = [{"Data": json.dumps(r, separators=(",", ":")).encode()} for r in rows[start:start + 400]]
        for wait in (0.2, 0.5, None):
            try:
                result = firehose.put_record_batch(DeliveryStreamName=os.environ["STREAM"], Records=records)
                records = [rec for rec, res in zip(records, result["RequestResponses"], strict=True)
                           if "ErrorCode" in res]
            except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError):
                pass  # the whole call refused (the stream over its limit): all of them, this time
            if not records or wait is None:
                break
            time.sleep(wait)
        failed += len(records)
    return failed


def reply(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}
