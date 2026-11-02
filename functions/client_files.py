"""The files people get: the installer (from the dashboard, or `make client` for MDM and other channels), with the
organisation's values filled in. Nothing secret goes in them."""

import json
import os

import models

CLIENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client")


def fill(text: str, values: dict) -> str:
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    return text


def read(name: str) -> str:
    with open(os.path.join(CLIENT, name)) as f:
        return f.read()


def otel_helper(values: dict, python: str = None, aws: str = None) -> str:
    """The helper, for this deployment. Python's and the AWS CLI's paths are filled in where they're known: here for
    managed settings, or else by the installer on the laptop."""
    helper = fill(read("otel-helper.py"), {"PROFILE": values["NAME"], "REGION": values["REGION"], "URL": values["URL"]})
    if python and aws:
        helper = helper.replace("#!__PYTHON__", f"#!{python} -I", 1).replace('"__AWS__"', json.dumps(aws), 1)
    return helper


def claude_settings(values: dict, aws: str, helper: str, picks: dict) -> dict:
    """The keys Claude Code needs, for ~/.claude/settings.json or managed settings. `picks` is models.choices():
    the allowed models as /model picker rows, and the admins' default."""
    profile = values["NAME"]
    tiers = models.tiers({t: values[f"MODEL_{t.upper()}"] for t in ("opus", "sonnet", "haiku")}, picks["options"])
    settings = {
        "env": {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": values["REGION"],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": tiers["opus"],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": tiers["sonnet"],
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": tiers["haiku"],
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_EXPORTER_OTLP_ENDPOINT": values["URL"],
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
            "OTEL_LOGS_EXPORT_INTERVAL": "30000",
            # The helper's identity is good for an hour, and after a sign-in telemetry should start again soon
            "CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS": "300000",
        },
        # Credentials for Claude Code's Bedrock calls only, signing in first when needed (the helper). AWS_PROFILE in
        # env would also point every aws command Claude runs for you at the Bedrock account.
        "awsCredentialExport": f"{helper} --credentials",
        "awsAuthRefresh": f"{aws} sso login --profile {profile}",
        "otelHeadersHelper": helper,
        # The tags the admins require, asked for at each session's start: changing them needs no new install
        "hooks": {event: [{"hooks": [{"type": "command", "command": f"{helper} --check-tags", "timeout": 30}]}]
                  for event in ("SessionStart", "UserPromptSubmit")},
    }
    if picks["options"]:
        # A row for each allowed model and routing, as models.choices() makes them. A model someone picks with /model
        # stays theirs.
        settings["modelPicker"] = {"options": picks["options"], "replaceBuiltInOptions": True}
    if picks["default"]:
        settings["env"]["ANTHROPIC_DEFAULT_MODEL"] = picks["default"]
    return settings


def install_script(values: dict, picks: dict) -> str:
    """The installer, with the settings and the helper inside it: one file to hand out. It fills in this
    machine's paths for __AWS__ and the helper."""
    settings = json.dumps(claude_settings(values, "__AWS__", "__HELPER__", picks), indent=2)
    script = read("install.sh").replace("@SETTINGS@", settings).replace("@HELPER@", otel_helper(values).rstrip("\n"))
    return fill(script, values)


def managed_settings(values: dict, picks: dict) -> str:
    """For MDM: the same settings as managed settings, which people can't override. MDM also puts the AWS CLI at
    its standard /usr/local/bin/aws and the helper at /usr/local/bin/claude-otel-helper; ~/.aws/config still needs
    the profile (dist/install-claude-code.sh --no-login, run as the person, writes it)."""
    settings = claude_settings(values, "/usr/local/bin/aws", "/usr/local/bin/claude-otel-helper", picks)
    return json.dumps({**settings, "allowedProviders": ["bedrock"]}, indent=2) + "\n"
