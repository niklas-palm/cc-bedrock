#!/bin/bash
# Sets up Claude Code on this Mac (or Linux machine) to use Claude through Amazon Bedrock in the
# @NAME@ AWS account, signing in with IAM Identity Center. Safe to run again.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash install-claude-code.sh [--desktop] [--no-login]

  --desktop        also set up the Claude Desktop app (macOS): opens a profile to install
  --no-login       don't open the Identity Center sign-in at the end

Tag your usage per repository, in its .claude/settings.json: { "env": { "CC_PROJECT": "apollo" } }.
Every CC_* variable there becomes a tag; CC_PROJECT=apollo claude does the same for one session.

What it changes, and nothing else:
  ~/.aws/config            adds [sso-session @NAME@] and [profile @NAME@]
  ~/.claude/settings.json  adds the Bedrock, model and telemetry settings, keeps everything else
  ~/.claude/cc-bedrock/    the telemetry helper
  ~/.local/                Claude Code and the AWS CLI, only if they aren't installed yet
EOF
}

# The organisation's values, filled in by the dashboard (or `make client`)
PROFILE="@NAME@"
START_URL="@START_URL@"
SSO_REGION="@SSO_REGION@"
ACCOUNT="@ACCOUNT@"
ROLE="@PERMISSION_SET@"
REGION="@REGION@"
SETTINGS=$(cat <<'JSON'
@SETTINGS@
JSON
)

LOGIN=1 DESKTOP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --desktop) DESKTOP=1; shift ;;
    --no-login) LOGIN=0; shift ;;
    *) usage; exit 1 ;;
  esac
done

command -v python3 >/dev/null || { echo "python3 is needed (macOS: xcode-select --install)." >&2; exit 1; }

# Claude Code and the AWS CLI, if they're missing: both into your home folder, so no admin rights needed
mkdir -p "$HOME/.local/bin"
PATH_BEFORE="$PATH"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v claude >/dev/null; then
  echo "Installing Claude Code..."
  curl -fsSL https://claude.ai/install.sh | bash
fi
if ! aws --version 2>/dev/null | grep -q "aws-cli/2"; then
  echo "Installing the AWS CLI..."
  tmp="$(mktemp -d)"
  if [ "$(uname)" = Darwin ]; then
    curl -fsSL https://awscli.amazonaws.com/AWSCLIV2.pkg -o "$tmp/AWSCLIV2.pkg"
    # The pkg's documented install for the current user only: into ~/.local/aws-cli
    cat > "$tmp/choices.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><array><dict>
  <key>choiceAttribute</key><string>customLocation</string>
  <key>attributeSetting</key><string>$HOME/.local</string>
  <key>choiceIdentifier</key><string>default</string>
</dict></array></plist>
XML
    installer -pkg "$tmp/AWSCLIV2.pkg" -target CurrentUserHomeDirectory -applyChoiceChangesXML "$tmp/choices.xml"
    ln -sf "$HOME/.local/aws-cli/aws" "$HOME/.local/bin/aws"
  else
    command -v unzip >/dev/null || { echo "unzip is needed to install the AWS CLI." >&2; exit 1; }
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "$tmp/awscli.zip"
    (cd "$tmp" && unzip -q awscli.zip)
    "$tmp/aws/install" -i "$HOME/.local/aws-cli" -b "$HOME/.local/bin" --update
  fi
  rm -rf "${tmp:?}"
fi
# By its full path in the settings, so Claude Code (and Claude Desktop) find it whatever their PATH is
AWS="$(command -v aws)"

DIR="$HOME/.claude/cc-bedrock"
mkdir -p "$DIR" "$HOME/.aws"
cat > "$DIR/otel-helper" <<'HELPER'
@HELPER@
HELPER
chmod +x "$DIR/otel-helper"

# ~/.aws/config: replace our two sections, leave the rest of the file as it is
python3 - "$HOME/.aws/config" "$PROFILE" "$START_URL" "$SSO_REGION" "$ACCOUNT" "$ROLE" "$REGION" <<'PY'
import os, re, sys
path, name, start_url, sso_region, account, role, region = sys.argv[1:]
text = open(path).read() if os.path.exists(path) else ""
ours = {f"[sso-session {name}]", f"[profile {name}]"}
kept, skipping = [], False
for line in text.splitlines():
    if re.match(r"\s*\[", line):
        skipping = line.strip() in ours
    if not skipping:
        kept.append(line)
sections = f"""
[sso-session {name}]
sso_start_url = {start_url}
sso_region = {sso_region}
sso_registration_scopes = sso:account:access

[profile {name}]
sso_session = {name}
sso_account_id = {account}
sso_role_name = {role}
region = {region}
"""
with open(path, "w") as f:
    f.write(("\n".join(kept).rstrip() + "\n" + sections).lstrip("\n"))
PY

# ~/.claude/settings.json: merge our keys in, with this machine's paths
python3 - "$HOME/.claude/settings.json" "$SETTINGS" "$AWS" "$DIR/otel-helper" <<'PY'
import json, os, shlex, sys
path, ours, aws, helper = sys.argv[1:]
ours = json.loads(ours)
for key in ("awsCredentialExport", "awsAuthRefresh"):
    ours[key] = ours[key].replace("__AWS__", shlex.quote(aws))
ours["otelHeadersHelper"] = helper
try:
    text = open(path).read() if os.path.exists(path) else ""
    settings = json.loads(text) if text.strip() else {}
except ValueError as error:
    sys.exit(f"{path} isn't valid JSON ({error}): fix it, or move it aside, and run this again")
if not isinstance(settings, dict):
    sys.exit(f"{path} isn't a JSON object: fix it, or move it aside, and run this again")
if not isinstance(settings.get("env"), dict):
    settings["env"] = {}
# What the admins may have since taken away: a default model, or the picker's list
settings["env"].pop("ANTHROPIC_DEFAULT_MODEL", None)
settings.pop("modelPicker", None)
settings["env"].update(ours.pop("env"))
settings.update(ours)
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PY

# Claude Desktop in third-party (Bedrock) mode reads its settings from a configuration profile. It shares the
# AWS profile, so one sign-in serves both, and sends its telemetry through the same helper.
if [ "$DESKTOP" = 1 ]; then
  [ "$(uname)" = Darwin ] || { echo "--desktop is for macOS." >&2; exit 1; }
  python3 - "$DIR/claude-desktop.mobileconfig" "$SETTINGS" "$AWS" "$DIR/otel-helper" "$PROFILE" <<'PY'
import json, plistlib, sys, uuid
path, settings, aws, helper, profile = sys.argv[1:]
settings = json.loads(settings)
env = settings["env"]
# The models Claude Code's picker offers, the default first, which is what Claude Desktop starts with
rows = settings.get("modelPicker", {}).get("options") or [
    {"model": env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"], "label": tier.title()} for tier in ("OPUS", "SONNET", "HAIKU")]
rows.sort(key=lambda row: row["model"] != env.get("ANTHROPIC_DEFAULT_MODEL"))
models = []
for row in rows:
    tier = next((t for t in ("opus", "sonnet", "haiku") if t in row["model"]), None)
    # Claude Code's [1m] suffix is supports1m in Claude Desktop
    models.append({"name": row["model"].removesuffix("[1m]"), "labelOverride": row["label"],
                   **({"anthropicFamilyTier": tier} if tier else {}),
                   **({"supports1m": True} if row["model"].endswith("[1m]") else {})})
desktop = {  # every value a string, JSON ones included: what Claude Desktop reads from a profile
    "inferenceProvider": "bedrock", "inferenceBedrockRegion": env["AWS_REGION"], "inferenceBedrockProfile": profile,
    "inferenceBedrockAwsCliPath": aws, "inferenceModels": json.dumps(models),
    "otlpEndpoint": env["OTEL_EXPORTER_OTLP_ENDPOINT"], "otlpProtocol": "http/json", "otlpHeadersHelper": helper}

def same_id(part):  # the same ids every time, so installing it again replaces it
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{env['OTEL_EXPORTER_OTLP_ENDPOINT']}/{part}")).upper()

payload = {**desktop, "PayloadType": "com.anthropic.claudefordesktop", "PayloadVersion": 1,
           "PayloadIdentifier": f"com.anthropic.claudefordesktop.{profile}", "PayloadUUID": same_id("payload"),
           "PayloadDisplayName": "Claude Desktop on Bedrock"}
with open(path, "wb") as f:
    plistlib.dump({"PayloadContent": [payload], "PayloadType": "Configuration", "PayloadVersion": 1,
                   "PayloadIdentifier": f"{profile}.claude-desktop", "PayloadUUID": same_id("profile"),
                   "PayloadScope": "User", "PayloadDisplayName": f"Claude Desktop: Bedrock ({profile})"}, f)
PY
  open "$DIR/claude-desktop.mobileconfig"
  echo "Claude Desktop: approve the profile in System Settings > General > Device Management, then restart Claude."
fi

if [ "$LOGIN" = 1 ] && ! "$AWS" sts get-caller-identity --profile "$PROFILE" >/dev/null 2>&1; then
  echo "Opening Identity Center to sign you in..."
  "$AWS" sso login --profile "$PROFILE"
fi

echo
echo "Done. Claude Code uses Bedrock in account $ACCOUNT."
echo "Start it with: claude    (when your sign-in expires, Claude Code opens the sign-in page again)"
case ":$PATH_BEFORE:" in *":$HOME/.local/bin:"*) ;; *)
  echo "If 'claude' isn't found in a new terminal, add ~/.local/bin to your PATH:"
  echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc    (or ~/.bashrc)" ;; esac
