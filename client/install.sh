#!/bin/bash
# Sets up Claude Code on this Mac (or Linux machine) to use Claude through Amazon Bedrock in account
# @ACCOUNT@ (the @NAME@ profile), signing in with IAM Identity Center. Safe to run again.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash install-claude-code.sh [--no-login]

  --no-login       don't open the Identity Center sign-in at the end

Tag your usage per repository, in its .claude/settings.json: { "env": { "CC_PROJECT": "apollo" } }.
Every CC_* variable there becomes a tag; CC_PROJECT=apollo claude does the same for one session.

What it changes, and nothing else:
  ~/.aws/config            adds [sso-session @NAME@] and [profile @NAME@]
  ~/.claude/settings.json  adds the Bedrock, model, telemetry and tag-check settings, keeps everything else
                           (your own hooks too) except a default model, a /model list or AWS credentials of
                           your own, which would take Claude Code elsewhere
  ~/.claude/cc-bedrock/    the telemetry helper, and its notes on the current sessions
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

LOGIN=1
while [ $# -gt 0 ]; do
  case "$1" in
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
# By its full path in the settings, so Claude Code finds it whatever its PATH is
AWS="$(command -v aws)"

DIR="$HOME/.claude/cc-bedrock"
mkdir -p "$DIR" "$HOME/.aws"
cat > "$DIR/otel-helper" <<'HELPER'
@HELPER@
HELPER
# This machine's Python and AWS CLI, by full path (the helper says why)
python3 - "$DIR/otel-helper" "$AWS" <<'PY'
import json, os, sys
path, aws = sys.argv[1:]
python = os.path.realpath(sys.executable)  # the interpreter itself, not a pyenv or asdf shim that reads PATH
if " " in python:
    sys.exit(f"Python is at {python}: a path with a space can't start a script. Use a Python whose path has none.")
text = open(path).read().replace("#!__PYTHON__", f"#!{python} -I", 1).replace('"__AWS__"', json.dumps(aws), 1)
open(path, "w").write(text)
PY
chmod +x "$DIR/otel-helper"

# ~/.aws/config: replace our two sections, leave the rest of the file as it is
python3 - "$HOME/.aws/config" "$PROFILE" "$START_URL" "$SSO_REGION" "$ACCOUNT" "$ROLE" "$REGION" <<'PY'
import os, re, sys
path, name, start_url, sso_region, account, role, region = sys.argv[1:]
text = open(path).read() if os.path.exists(path) else ""
ours = {f"[sso-session {name}]", f"[profile {name}]"}
kept, skipping, own = [], False, ""
for line in text.splitlines():
    if re.match(r"\s*\[", line):
        skipping = line.strip() in ours
    if not skipping:
        kept.append(line)
    elif re.match(r"\s*ca_bundle\s*=", line):  # a TLS-inspecting proxy's certificate, theirs to keep
        own = line.strip() + "\n"
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
{own}"""
with open(path, "w") as f:
    f.write(("\n".join(kept).rstrip() + "\n" + sections).lstrip("\n"))
PY

# ~/.claude/settings.json: merge our keys in, with this machine's paths
python3 - "$HOME/.claude/settings.json" "$SETTINGS" "$AWS" "$DIR/otel-helper" <<'PY'
import json, os, shlex, sys
path, ours, aws, helper = sys.argv[1:]
ours = json.loads(ours)
ours["awsAuthRefresh"] = ours["awsAuthRefresh"].replace("__AWS__", shlex.quote(aws))
ours["awsCredentialExport"] = ours["awsCredentialExport"].replace("__HELPER__", shlex.quote(helper))
ours["otelHeadersHelper"] = shlex.quote(helper)  # a command line: a home with a space in it still works
for groups in ours["hooks"].values():
    for group in groups:
        for hook in group["hooks"]:
            hook["command"] = hook["command"].replace("__HELPER__", shlex.quote(helper))
try:
    text = open(path).read() if os.path.exists(path) else ""
    settings = json.loads(text) if text.strip() else {}
except ValueError as error:
    sys.exit(f"{path} isn't valid JSON ({error}): fix it, or move it aside, and run this again")
if not isinstance(settings, dict):
    sys.exit(f"{path} isn't a JSON object: fix it, or move it aside, and run this again")
if not isinstance(settings.get("env"), dict):
    settings["env"] = {}
# A model or /model list of the person's own would hide the admins' (and so would what the admins have since
# taken away): theirs replace it
for key in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL"):
    settings["env"].pop(key, None)
# AWS credentials of their own in Claude Code's settings would take it to another account, outside the budgets,
# whenever they're signed out here
for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
            "AWS_BEARER_TOKEN_BEDROCK"):
    settings["env"].pop(key, None)
for key in ("model", "availableModels", "modelPicker"):
    settings.pop(key, None)
settings["env"].update(ours.pop("env"))
# Our hooks beside the person's own: theirs stay, ours replace the ones an earlier install added. Where managed
# settings (MDM) already run them, adding them here too would say everything twice.
def is_ours(hook):
    return isinstance(hook, dict) and str(hook.get("command", "")).endswith(" --check-tags")


def managed_has_hooks():
    for managed in ("/Library/Application Support/ClaudeCode/managed-settings.json",
                    "/etc/claude-code/managed-settings.json"):
        try:
            if "--check-tags" in open(managed).read():
                return True
        except (OSError, ValueError):  # unreadable, or not text: then it doesn't carry ours
            pass
    return False


if not isinstance(settings.get("hooks"), dict):
    settings["hooks"] = {}
skip = managed_has_hooks()
for event, groups in ours.pop("hooks").items():
    current = settings["hooks"].get(event, [])
    if not isinstance(current, list):
        print(f"Left {path}'s hooks.{event} as it is: it isn't a list, so the tag check isn't added there.")
        continue
    kept = []
    for group in current:
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            group = dict(group, hooks=[h for h in group["hooks"] if not is_ours(h)])
            if not group["hooks"]:
                continue
        kept.append(group)
    settings["hooks"][event] = kept + ([] if skip else groups)
    if not settings["hooks"][event]:
        del settings["hooks"][event]
settings.update(ours)
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PY

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
