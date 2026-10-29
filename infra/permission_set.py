"""`make permission-set`: creates or updates the Claude Code permission set in IAM Identity Center and assigns it to
the config's groups in the Bedrock account. Run it with the credentials of whoever administers Identity Center (the
management account, or its delegated administrator), after `make deploy`: the permission set refers to the three
policies the stack created.

It takes the region's Identity Center instance and finds the groups' IDs from their names, so config.yaml can
stay readable.
"""

import os
import subprocess
import sys

import boto3
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.environ.get("CONFIG", os.path.join(ROOT, "config.yaml"))) as f:
    config = yaml.safe_load(f)
identity_center = config["identity_center"]
region = identity_center["region"]

instances = boto3.client("sso-admin", region_name=region).list_instances()["Instances"]
if not instances:
    sys.exit(f"No Identity Center instance in {region}: run this with the Identity Center administrator's credentials")
instance = instances[0]

store = boto3.client("identitystore", region_name=region)
group_ids = []
for group in identity_center["groups"]:
    try:
        group_ids.append(store.get_group_id(IdentityStoreId=instance["IdentityStoreId"], AlternateIdentifier={
            "UniqueAttribute": {"AttributePath": "displayName", "AttributeValue": group}})["GroupId"])
    except store.exceptions.ResourceNotFoundException:
        sys.exit(f"Identity Center has no group called {group!r}")

# The template loops over the groups with Fn::ForEach, a transform CloudFormation runs only when allowed to
subprocess.run(["aws", "cloudformation", "deploy", "--region", region, "--no-fail-on-empty-changeset",
                "--capabilities", "CAPABILITY_AUTO_EXPAND",
                "--template-file", os.path.join(ROOT, "idc", "permission-set.yaml"),
                "--stack-name", f"{config['name']}-permission-set", "--parameter-overrides",
                f"InstanceArn={instance['InstanceArn']}", f"AccountId={config['account']}",
                f"GroupIds={','.join(group_ids)}", f"PermissionSetName={identity_center['permission_set']}",
                f"PolicyPrefix={config['name']}",
                f"SessionDuration={identity_center.get('session_duration', 'PT12H')}"], check=True)
print(f"Permission set {identity_center['permission_set']} assigned to {', '.join(identity_center['groups'])}")
