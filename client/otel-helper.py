#!/usr/bin/env python3
"""Claude Code runs this (`otelHeadersHelper`) at start and every 5 minutes (the installer sets
CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS), and sends the JSON it prints as headers with its telemetry:

  x-cc-identity  a presigned STS GetCallerIdentity URL, signed with your Identity Center credentials. The collector
                 calls it to learn who you are; it can do nothing else with it, and it expires in an hour.
  x-cc-tags      every CC_* environment variable as a tag, JSON: CC_PROJECT=apollo is {"project": "apollo"}.
                 Claude Code passes on the `env` of its settings files, so a repository's .claude/settings.json
                 decides its own tags; none set, no tags.

As a hook (`otel-helper --check-tags`, at a session's start and on each prompt) it asks the dashboard which tags
the admins require, with the same identity, and says which are missing; when the admins require them, it holds
prompts back until they're set. If it can't ask (signed out, offline), it lets you work.

Standard library only, plus the AWS CLI for the credentials.
"""

import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

PROFILE = "@PROFILE@"
REGION = "@REGION@"
URL = "@URL@"
# What the dashboard said at each session's start, so a prompt doesn't wait for it again
SESSIONS = os.path.expanduser("~/.claude/cc-bedrock/sessions")
# The AWS CLI wherever its installers put it, whatever PATH this runs with
AWS = shutil.which("aws", path=os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(
    [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"])) or "aws"


def tags() -> dict:
    return {name[3:].lower(): value for name, value in os.environ.items() if name.startswith("CC_") and value}


def presigned_identity_url(creds: dict, now: datetime.datetime) -> str:
    """SigV4 query-string signing of GET https://sts.<region>.amazonaws.com/?Action=GetCallerIdentity."""
    host = f"sts.{REGION}.amazonaws.com"
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{day}/{REGION}/sts/aws4_request"
    params = {"Action": "GetCallerIdentity", "Version": "2011-06-15", "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
              "X-Amz-Credential": f"{creds['AccessKeyId']}/{scope}", "X-Amz-Date": amz_date, "X-Amz-Expires": "3600",
              "X-Amz-SignedHeaders": "host"}
    if creds.get("SessionToken"):
        params["X-Amz-Security-Token"] = creds["SessionToken"]
    query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                     for k, v in sorted(params.items()))
    canonical = f"GET\n/\n{query}\nhost:{host}\n\nhost\n{hashlib.sha256(b'').hexdigest()}"
    to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = ("AWS4" + creds["SecretAccessKey"]).encode()
    for part in (day, REGION, "sts", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}/?{query}&X-Amz-Signature={signature}"


def identity() -> str:
    out = subprocess.run([AWS, "configure", "export-credentials", "--profile", PROFILE, "--format", "process"],
                         capture_output=True, text=True, timeout=20, check=True).stdout
    return presigned_identity_url(json.loads(out), datetime.datetime.now(datetime.timezone.utc))


def required() -> dict:
    """{"tags": {"project": "require", "team": "warn"}}, as the admins set it on the dashboard."""
    request = urllib.request.Request(f"{URL}/v1/tags", headers={"x-cc-identity": identity()})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def missing_tags_message(missing: dict) -> str:
    """What to tell someone whose session lacks these tags, {tag: "warn" or "require"}."""
    required = [t for t, mode in missing.items() if mode == "require"]
    asked = [t for t, mode in missing.items() if mode != "require"]

    def the(tags):
        return ("the tag " if len(tags) == 1 else "the tags ") + ", ".join(tags)
    wants = " and ".join(([f"requires {the(required)}"] if required else []) +
                         ([f"asks for {the(asked)}"] if asked else []))
    variables = ", ".join(f"CC_{t.upper()}" for t in missing)
    example = ", ".join(f'"CC_{t.upper()}": "..."' for t in missing)
    inline = " ".join(f"CC_{t.upper()}=..." for t in missing)
    where = f"the repository's .claude/settings.json ({{\"env\": {{{example}}}}})"
    if required:
        waits = f"{', '.join(required)} {'is' if len(required) == 1 else 'are'} set"
        return (f"Your organisation {wants} on Claude Code usage, so prompts wait until {waits}. "
                f"Set {variables} in {where} and start Claude again, or start it as {inline} claude.")
    return (f"Your organisation {wants} on Claude Code usage. "
            f"Set {variables} in {where}, or start Claude as {inline} claude.")


def check_tags(hook: dict):
    """The hook: at a session's start, says which of the admins' tags are missing; on a prompt, holds it back while
    a required one is. Claude Code reads what it prints."""
    os.makedirs(SESSIONS, exist_ok=True)
    saved = os.path.join(SESSIONS, re.sub(r"[^\w-]", "", hook.get("session_id", "")) + ".json")
    starting = hook.get("hook_event_name") == "SessionStart"
    try:
        if starting or not os.path.exists(saved):
            rules = required()
            with open(saved, "w") as f:
                json.dump(rules, f)
        else:
            with open(saved) as f:
                rules = json.load(f)
    except (OSError, ValueError, subprocess.SubprocessError):
        return  # couldn't ask: a guardrail doesn't stop work when the network does
    if starting:  # forget sessions over a week old
        for name in os.listdir(SESSIONS):
            if os.path.getmtime(os.path.join(SESSIONS, name)) < time.time() - 7 * 86400:
                os.remove(os.path.join(SESSIONS, name))
    rules = rules.get("tags") if isinstance(rules.get("tags"), dict) else {}
    have = tags()
    missing = {t: mode for t, mode in rules.items() if t not in have and mode in ("warn", "require")}
    required_missing = {t: mode for t, mode in missing.items() if mode == "require"}
    if starting and missing:
        print(json.dumps({"systemMessage": missing_tags_message(missing)}))
    elif not starting and required_missing:
        print(json.dumps({"decision": "block", "reason": missing_tags_message(required_missing)}))


def main():
    if "--check-tags" in sys.argv:
        try:
            hook = json.load(sys.stdin)
        except ValueError:
            hook = {}
        return check_tags(hook)
    headers = {"x-cc-tags": json.dumps(tags())}
    try:
        headers["x-cc-identity"] = identity()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Signed out. Failing makes Claude Code say telemetry isn't being sent (in /status); it runs this again
        # within 5 minutes, so telemetry starts again soon after you sign in
        print(f"otel-helper: no AWS credentials for profile {PROFILE} (sign in: aws sso login --profile {PROFILE})"
              f": {error}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(headers))


if __name__ == "__main__":
    main()
