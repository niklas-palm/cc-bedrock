"""The CDK app: reads config.yaml and deploys the stack (and, with a custom domain, its certificate in us-east-1)."""

import os
import sys

import aws_cdk as cdk
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from stack import CertificateStack, ClaudeCodeStack  # noqa: E402

with open(os.environ.get("CONFIG", "config.yaml")) as f:
    config = yaml.safe_load(f)
account = str(config["account"])  # CDK refuses to deploy if your credentials are for another account
app = cdk.App()

certificate = None
if config.get("domain"):
    certificate = CertificateStack(app, f"{config['name']}-certificate", config=config,
                                   env=cdk.Environment(account=account, region="us-east-1"),
                                   cross_region_references=True).certificate

ClaudeCodeStack(app, config["name"], config=config, certificate=certificate,
                env=cdk.Environment(account=account, region=config["region"]),
                cross_region_references=bool(certificate))

for key, value in (config.get("tags") or {}).items():
    cdk.Tags.of(app).add(key, str(value))
app.synth()
