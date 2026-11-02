#!__PYTHON__
"""Claude Code runs this (`otelHeadersHelper`) at start and every 5 minutes (the installer sets
CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS), and sends the JSON it prints as headers with its telemetry:

  x-cc-identity  a presigned STS GetCallerIdentity URL, signed with your Identity Center credentials. The collector
                 calls it to learn who you are; it can do nothing else with it, and it expires in an hour.
  x-cc-tags      every CC_* environment variable as a tag, JSON: CC_PROJECT=apollo is {"project": "apollo"}.
                 Claude Code passes on the `env` of its settings files, so a repository's .claude/settings.json
                 decides its own tags; none set, no tags.

As `awsCredentialExport` (`otel-helper --credentials`) it gives Claude Code the Bedrock role's credentials, and
when you're signed out it opens the Identity Center sign-in first: otherwise Claude Code would go on with whatever
other AWS credentials the laptop has, in another account, outside the budgets.

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
import pwd
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

PROFILE = "@PROFILE@"
REGION = "@REGION@"
URL = "@URL@"
# Claude Code passes a repository's settings env (PATH, HOME, PYTHONPATH...) to this helper and its hooks, and a
# repository mustn't be able to make it run a program of its own. So Python is the real interpreter, isolated (-I) and
# without `site` (-S: macOS Pythons read PYTHONEXECUTABLE even when isolated, and `site` would run the .pth files of
# the virtual environment it names), in the first line the installer fills in; the AWS CLI is the one the installer
# found, and it runs with an environment of its own: this account's home, not $HOME, and none of the repository's
# AWS_* or PYTHON*.
HOME = pwd.getpwuid(os.getuid()).pw_dir
AWS = "__AWS__"
# The display's variables let `aws sso login` open a browser on a Linux desktop; BROWSER isn't one of them, since
# it names a program to run.
CLEAN = {"HOME": HOME, "PATH": "/usr/bin:/bin", **{k: v for k, v in os.environ.items() if k in (
    "LANG", "LC_ALL", "TMPDIR", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
    "AWS_CA_BUNDLE", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")}}
# What the dashboard said at each session's start, so a prompt doesn't wait for it again
SESSIONS = os.path.join(HOME, ".claude", "cc-bedrock", "sessions")
# There while the credentials command signs someone in, so the telemetry helper, which Claude Code starts at the
# same moment, waits for the sign-in instead of failing
SIGNING_IN = os.path.join(HOME, ".claude", "cc-bedrock", "signing-in")


# What the collector keeps (functions/ingest.py): a value it would drop doesn't count as the tag being set
TAG_KEY = re.compile(r"[a-z0-9_]{1,32}")
TAG_VALUE = re.compile(r"[\w .:/@+=-]{1,100}")


def tags() -> dict:
    found = {name[3:].lower(): value for name, value in os.environ.items() if name.startswith("CC_")}
    return {k: v for k, v in found.items() if TAG_KEY.fullmatch(k) and TAG_VALUE.fullmatch(v)}


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


def export() -> str:
    return subprocess.run([AWS, "configure", "export-credentials", "--profile", PROFILE, "--format", "process"],
                          capture_output=True, text=True, timeout=20, check=True, env=CLEAN).stdout


def identity() -> str:
    return presigned_identity_url(json.loads(export()), datetime.datetime.now(datetime.timezone.utc))


def credentials():
    """For Claude Code's Bedrock calls. Signed out, sign in first: Claude Code waits for it."""
    # Before trying, so the telemetry helper sees it however fast it starts. Only a hint: with MDM the helper may
    # run where ~/.claude/cc-bedrock doesn't exist, and nothing here may stop Claude Code getting credentials.
    try:
        os.makedirs(os.path.dirname(SIGNING_IN), exist_ok=True)
        open(SIGNING_IN, "w").close()
    except OSError:
        pass
    try:
        try:
            out = export()
        except subprocess.CalledProcessError:
            # The sign-in's messages go to stderr: what this prints on stdout is the credentials
            subprocess.run([AWS, "sso", "login", "--profile", PROFILE], stdout=sys.stderr, timeout=300, check=True,
                           env=CLEAN)
            out = export()
    finally:
        try:
            os.remove(SIGNING_IN)
        except OSError:
            pass  # two at once: the other one removed it
    print(out)


def signing_in() -> bool:
    try:
        return time.time() - os.path.getmtime(SIGNING_IN) < 300  # older is left over from a sign-in cut short
    except OSError:
        return False


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
    unusable = [f"CC_{t.upper()}" for t in missing if os.environ.get(f"CC_{t.upper()}")]
    if unusable:  # set, but not as a tag can be
        wants += (f" ({', '.join(unusable)} {'is' if len(unusable) == 1 else 'are'} set, but a tag is letters, digits,"
                  " spaces and . : / @ + = - _, up to 100 characters)")
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
    saved = os.path.join(SESSIONS, re.sub(r"[^\w-]", "", str(hook.get("session_id", ""))) + ".json")
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
            try:
                if os.path.getmtime(os.path.join(SESSIONS, name)) < time.time() - 7 * 86400:
                    os.remove(os.path.join(SESSIONS, name))
            except OSError:
                pass  # another session's start got there first
    # Only tag names and modes the dashboard can save: anything else in the answer isn't shown to people
    rules = rules.get("tags") if isinstance(rules, dict) and isinstance(rules.get("tags"), dict) else {}
    rules = {t: mode for t, mode in rules.items() if TAG_KEY.fullmatch(t) and mode in ("warn", "require")}
    have = tags()
    missing = {t: mode for t, mode in rules.items() if t not in have}
    required_missing = {t: mode for t, mode in missing.items() if mode == "require"}
    if starting and missing:
        print(json.dumps({"systemMessage": missing_tags_message(missing)}))
    elif not starting and required_missing:
        print(json.dumps({"decision": "block", "reason": missing_tags_message(required_missing)}))


def main():
    if "--credentials" in sys.argv:
        return credentials()
    if "--check-tags" in sys.argv:
        try:
            hook = json.load(sys.stdin)
        except ValueError:
            hook = {}
        return check_tags(hook if isinstance(hook, dict) else {})
    headers = {"x-cc-tags": json.dumps(tags())}
    # A sign-in under way: wait for it, within the 30 s or so Claude Code gives a helper
    deadline = time.time() + 25
    while True:
        try:
            headers["x-cc-identity"] = identity()
            break
        except (OSError, ValueError, subprocess.SubprocessError):
            if not (signing_in() and time.time() < deadline):
                # Signed out. Failing makes Claude Code say telemetry isn't being sent; it runs this again within
                # 5 minutes, so telemetry starts again soon after you sign in. Claude Code cuts the message short:
                # what matters goes first.
                print(f"not signed in to {PROFILE}, so telemetry waits: it starts within 5 minutes of signing in "
                      f"(aws sso login --profile {PROFILE})", file=sys.stderr)
                sys.exit(1)
            time.sleep(2)
    print(json.dumps(headers))


if __name__ == "__main__":
    main()
