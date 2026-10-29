"""Creates the CUR 2.0 export the dashboard reads billed cost from: the Bedrock account's own costs, daily, with the
caller's IAM principal (who made each Bedrock call). Data Exports lives in us-east-1 only, and CloudFormation can't
create it from the stack's region, so `make deploy` runs this. Safe to run again: an existing export is pointed
at the stack's bucket if it isn't already (after a `make destroy` and a new deploy, say).
"""

import json
import os

import boto3
import yaml
from stack import CUR_COLUMNS, cur_export

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.environ.get("CONFIG", os.path.join(ROOT, "config.yaml"))) as f:
        config = yaml.safe_load(f)
    outputs = boto3.client("cloudformation", region_name=config["region"]).describe_stacks(
        StackName=config["name"])["Stacks"][0]["Outputs"]
    bucket = next(o["OutputValue"] for o in outputs if o["OutputKey"] == "DataBucket")

    name = cur_export(config)
    export = {
        "Name": name,
        "Description": "Bedrock cost by IAM principal, for the Claude Code dashboard",
        "DataQuery": {
            "QueryStatement": f"SELECT {', '.join(CUR_COLUMNS)} FROM COST_AND_USAGE_REPORT",
            "TableConfigurations": {"COST_AND_USAGE_REPORT": {
                "TIME_GRANULARITY": "DAILY", "INCLUDE_IAM_PRINCIPAL_DATA": "TRUE", "INCLUDE_RESOURCES": "FALSE",
                "INCLUDE_SPLIT_COST_ALLOCATION_DATA": "FALSE", "INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY": "FALSE"}}},
        "DestinationConfigurations": {"S3Destination": {
            "S3Bucket": bucket, "S3Prefix": "cur", "S3Region": config["region"],
            "S3OutputConfigurations": {"OutputType": "CUSTOM", "Format": "PARQUET", "Compression": "PARQUET",
                                       "Overwrite": "OVERWRITE_REPORT"}}},
        "RefreshCadence": {"Frequency": "SYNCHRONOUS"},
    }

    billing_account = str(config.get("billing_account") or config["account"])
    if billing_account != str(config["account"]):
        print(f"Your exports live in {billing_account}: create this one there, with its credentials:\n"
              f"  aws bcm-data-exports create-export --region us-east-1 --export '{json.dumps(export)}'")
        return

    exports = boto3.client("bcm-data-exports", region_name="us-east-1")
    existing = next((e for e in exports.list_exports()["Exports"] if e["ExportName"] == name), None)
    if not existing:
        exports.create_export(Export=export)
        print(f"Created CUR export {name}: the first data arrives within 24 hours")
        return
    current = exports.get_export(ExportArn=existing["ExportArn"])["Export"]
    if current["DestinationConfigurations"]["S3Destination"]["S3Bucket"] == bucket:
        print(f"CUR export {name} exists")
        return
    exports.update_export(ExportArn=existing["ExportArn"], Export=export)
    print(f"Pointed CUR export {name} at {bucket}: its data arrives there within 24 hours")


if __name__ == "__main__":
    main()
