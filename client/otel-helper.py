#!/usr/bin/env python3
"""Claude Code runs this (`otelHeadersHelper`) at start and every 29 minutes, and sends the JSON it prints as
headers with its telemetry:

  x-cc-identity  a presigned STS GetCallerIdentity URL, signed with your Identity Center credentials. The collector
                 calls it to learn who you are; it can do nothing else with it, and it expires in an hour.
  x-cc-project   the project your usage is counted against: $CC_PROJECT if set (a repository sets it in its
                 .claude/settings.json `env`, which Claude Code passes on to us), else what the installer's
                 --project last wrote, else "default".

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
PROJECT_FILE = os.path.expanduser("~/.claude/cc-bedrock/project")
# Claude Desktop starts helpers with a short PATH, without Homebrew's
AWS = shutil.which("aws", path=os.environ.get("PATH", "") + os.pathsep + os.pathsep.join(
    [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"])) or "aws"


def project() -> str:
    if os.environ.get("CC_PROJECT"):
        return os.environ["CC_PROJECT"]
    try:
        return open(PROJECT_FILE).read().strip() or "default"
    except OSError:
        return "default"


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
    headers = {"x-cc-project": project()}
    try:
        out = subprocess.run([AWS, "configure", "export-credentials", "--profile", PROFILE, "--format", "process"],
                             capture_output=True, text=True, timeout=20, check=True).stdout
        headers["x-cc-identity"] = presigned_identity_url(json.loads(out), datetime.datetime.now(datetime.timezone.utc))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Signed out: telemetry is refused until you sign in again (Claude Code asks you to)
        print(f"otel-helper: no AWS credentials for profile {PROFILE}: {error}", file=sys.stderr)
    print(json.dumps(headers))


if __name__ == "__main__":
    main()
