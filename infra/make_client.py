"""`make client`: the files for MDM or any other channel, the same ones the dashboard hands out.

    dist/install-claude-code.sh   what people run (or MDM runs as them)
    dist/managed-settings.json    the same settings as managed settings, which people can't override
    dist/claude-otel-helper       the helper those managed settings point at (/usr/local/bin/claude-otel-helper)
"""

import json
import os
import sys

import boto3
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "functions"))
import client_files  # noqa: E402
import models  # noqa: E402

with open(os.environ.get("CONFIG", os.path.join(ROOT, "config.yaml"))) as f:
    config = yaml.safe_load(f)
outputs = boto3.client("cloudformation", region_name=config["region"]).describe_stacks(
    StackName=config["name"])["Stacks"][0]["Outputs"]
values = client_files.values({
    "NAME": config["name"], "ACCOUNT": str(config["account"]), "REGION": config["region"],
    "SSO_START_URL": config["identity_center"]["start_url"], "SSO_REGION": config["identity_center"]["region"],
    "PERMISSION_SET": config["identity_center"]["permission_set"],
    "URL": next(o["OutputValue"] for o in outputs if o["OutputKey"] == "Url"),
    "MODEL_OPUS": config["models"]["opus"], "MODEL_SONNET": config["models"]["sonnet"],
    "MODEL_HAIKU": config["models"]["haiku"]})

# The models the admins allow, and their default, as the dashboard hands them out
bucket = next(o["OutputValue"] for o in outputs if o["OutputKey"] == "DataBucket")
s3 = boto3.client("s3", region_name=config["region"])
try:
    chosen = json.loads(s3.get_object(Bucket=bucket, Key="config/models.json")["Body"].read())
except s3.exceptions.NoSuchKey:
    chosen = {}
picks = models.choices(models.available(boto3.client("bedrock", region_name=config["region"])),
                       chosen.get("disabled", []), chosen.get("default", ""), config["models"],
                       chosen.get("routes_off", []))

files = {"install-claude-code.sh": client_files.install_script(values, picks),
         "managed-settings.json": client_files.managed_settings(values, picks),
         "claude-otel-helper": client_files.otel_helper(values)}
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
for name, text in files.items():
    path = os.path.join(ROOT, "dist", name)
    with open(path, "w") as f:
        f.write(text)
    if not name.endswith(".json"):
        os.chmod(path, 0o755)
    print(path)
