#!/usr/bin/env python3
"""Claude Code runs this (`otelHeadersHelper`) at start and every 5 minutes (the installer sets
CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS), and sends the JSON it prints as headers with its telemetry:

  x-cc-identity  a presigned STS GetCallerIdentity URL, signed with your Identity Center credentials. The collector
                 calls it to learn who you are; it can do nothing else with it, and it expires in an hour.
  x-cc-tags      every CC_* environment variable as a tag, JSON: CC_PROJECT=apollo is {"project": "apollo"}.
                 Claude Code passes on the `env` of its settings files, so a repository's .claude/settings.json
                 decides its own tags; none set, no tags.

Standard library only, plus the AWS CLI for the credentials.
"""

import datetime
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import urllib.parse

PROFILE = "@PROFILE@"
REGION = "@REGION@"
# Claude Desktop starts helpers with a short PATH, without Homebrew's
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


def main():
    headers = {"x-cc-tags": json.dumps(tags())}
    try:
        out = subprocess.run([AWS, "configure", "export-credentials", "--profile", PROFILE, "--format", "process"],
                             capture_output=True, text=True, timeout=20, check=True).stdout
        headers["x-cc-identity"] = presigned_identity_url(json.loads(out), datetime.datetime.now(datetime.timezone.utc))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Signed out. Failing makes Claude Code say telemetry isn't being sent (in /status); it runs this again
        # within 5 minutes, so telemetry starts again soon after you sign in
        print(f"otel-helper: no AWS credentials for profile {PROFILE} (sign in: aws sso login --profile {PROFILE})"
              f": {error}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(headers))


if __name__ == "__main__":
    main()
