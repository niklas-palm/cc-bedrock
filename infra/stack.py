"""Everything on the AWS side, in the Bedrock account.

    laptop  ── OTLP/HTTP ──► CloudFront /v1/*  ─► HTTP API ─► ingest λ ─► Firehose ─► S3 telemetry/
    browser ─────────────► CloudFront /*     ─► S3 (the dashboard)
                           CloudFront /api/* ─► HTTP API (JWT) ─► api λ ─► Athena ◄─ S3 telemetry/ and cur/
    every 10 minutes:      enforcer λ ─► Athena ─► IAM policies <name>-over-budget, <name>-models-off
    AWS Data Exports (CUR 2.0) ─► S3 cur/
"""

import json
import os

import aws_cdk as cdk
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_athena as athena
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kinesisfirehose as firehose
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_ssm as ssm
from constructs import Construct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVOKE = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]

TELEMETRY_COLUMNS = {"ts": "bigint", "user": "string", "tags": "map<string,string>", "kind": "string",
                     "name": "string", "type": "string", "value": "double", "session": "string", "model": "string",
                     "attrs": "map<string,string>"}
CUR_COLUMNS = {"line_item_usage_start_date": "timestamp", "line_item_product_code": "string",
               "line_item_usage_type": "string", "line_item_iam_principal": "string",
               "line_item_usage_amount": "double", "line_item_unblended_cost": "double"}


def cur_export(config: dict) -> str:
    """The CUR 2.0 export's name: infra/cur_export.py creates it, the cur table reads where it writes."""
    return f"{config['name']}-bedrock-by-principal"


def telemetry_roles(config: dict) -> list:
    """Role name patterns whose sessions are people: they may send telemetry, and count in the bill and budgets.
    Identity Center names the role AWSReservedSSO_<permission set>_<16 characters>."""
    permission_set = config["identity_center"]["permission_set"]
    roles = config.get("telemetry_roles") or [f"AWSReservedSSO_{permission_set}_{'?' * 16}"]
    if not isinstance(roles, list):  # a string would be read a character at a time, and its "*" is every role
        raise ValueError("telemetry_roles in config.yaml is a list of role name patterns, like [\"MyRole_*\"]")
    return roles


def installer_values(config: dict, account: str, region: str) -> dict:
    """What the installer is filled in with (client/install.sh's @NAME@ and so on), apart from the dashboard's URL.
    The api function gets them as its environment; `make client` uses them as they are."""
    identity_center, models = config["identity_center"], config["models"]
    return {"NAME": config["name"], "ACCOUNT": str(account), "REGION": region,
            "START_URL": identity_center["start_url"], "SSO_REGION": identity_center["region"],
            "PERMISSION_SET": identity_center["permission_set"],
            "MODEL_OPUS": models["opus"], "MODEL_SONNET": models["sonnet"], "MODEL_HAIKU": models["haiku"]}


def hosted_zone(scope: Construct, domain: dict):
    return route53.HostedZone.from_hosted_zone_attributes(
        scope, "Zone", hosted_zone_id=domain["hosted_zone_id"], zone_name=domain["hosted_zone_name"])


class CertificateStack(cdk.Stack):
    """CloudFront only takes certificates from us-east-1."""

    def __init__(self, scope: Construct, id: str, *, config: dict, **kwargs):
        super().__init__(scope, id, **kwargs)
        domain = config["domain"]
        self.certificate = acm.Certificate(self, "Certificate", domain_name=domain["name"],
                                           validation=acm.CertificateValidation.from_dns(hosted_zone(self, domain)))


class ClaudeCodeStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, *, config: dict, certificate=None, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.config = config
        self.name = config["name"]

        controls = self.identity_center_policies()  # (over budget, models off)
        data, stream, database = self.storage()
        ingest, api_fn = self.functions(data, stream, database, controls)
        self.site(ingest, api_fn, certificate)

    def identity_center_policies(self) -> tuple:
        """The permission set refers to these three policies by name, so what the role may do is managed here, in
        the Bedrock account, and changes without re-provisioning the permission set."""
        iam.ManagedPolicy(self, "BedrockPolicy", managed_policy_name=f"{self.name}-bedrock", statements=[
            iam.PolicyStatement(actions=INVOKE, resources=[
                f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                f"arn:aws:bedrock:*:{self.account}:application-inference-profile/*",
                "arn:aws:bedrock:*::foundation-model/anthropic.*"]),
            iam.PolicyStatement(actions=["bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile"],
                                resources=["*"]),
            # The first call to a model the account hasn't used yet makes Bedrock subscribe to it, as the caller
            iam.PolicyStatement(actions=["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe"],
                                resources=["*"],
                                conditions={"StringEquals": {"aws:CalledViaLast": "bedrock.amazonaws.com"}}),
        ])
        # The enforcer rewrites these two with the admins' controls. Two, because Bedrock's refusal names the
        # policy that denied, so people see why: over budget, or a model turned off. CloudFormation leaves them
        # alone after creating them, unless these starting documents change.
        over_budget = iam.ManagedPolicy(self, "OverBudgetPolicy", managed_policy_name=f"{self.name}-over-budget",
                                        statements=[iam.PolicyStatement(
                                            effect=iam.Effect.DENY, actions=INVOKE, resources=["*"],
                                            conditions={"StringLike": {"aws:userid": ["nobody-is-over-budget"]}})])
        models_off = iam.ManagedPolicy(self, "ModelsOffPolicy", managed_policy_name=f"{self.name}-models-off",
                                       statements=[iam.PolicyStatement(
                                           effect=iam.Effect.DENY, actions=INVOKE,
                                           resources=["arn:aws:bedrock:*::foundation-model/no-model-is-off"])])
        return over_budget, models_off

    def storage(self):
        retention = cdk.Duration.days(self.config.get("retention_days", 730))
        data = self.data = s3.Bucket(self, "Data", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                         removal_policy=cdk.RemovalPolicy.RETAIN, lifecycle_rules=[
                             s3.LifecycleRule(prefix="athena-results/", expiration=cdk.Duration.days(7)),
                             s3.LifecycleRule(prefix="telemetry/", expiration=retention),
                             s3.LifecycleRule(prefix="telemetry-errors/", expiration=retention)])

        # AWS Data Exports writes the CUR 2.0 export under cur/, from this account or, with billing_account, from
        # the one your organisation keeps its exports in. It checks this policy on the whole bucket when the export
        # is created, so a cur/* resource is refused.
        exporters = sorted({self.account, str(self.config.get("billing_account") or self.account)})
        data.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal("bcm-data-exports.amazonaws.com"),
                        iam.ServicePrincipal("billingreports.amazonaws.com")],
            actions=["s3:PutObject", "s3:GetBucketPolicy"], resources=[data.bucket_arn, data.arn_for_objects("*")],
            conditions={"StringEquals": {"aws:SourceAccount": exporters}, "StringLike": {"aws:SourceArn": [
                arn for account in exporters for arn in (f"arn:aws:cur:us-east-1:{account}:definition/*",
                                                          f"arn:aws:bcm-data-exports:us-east-1:{account}:export/*")]}}))

        database = self.name.replace("-", "_")
        self.database = glue.CfnDatabase(self, "Database", catalog_id=self.account,
                                         database_input=glue.CfnDatabase.DatabaseInputProperty(name=database))
        # One row per metric data point or event: Claude Code's attributes in `attrs`, plus the verified user and
        # the sender's CC_* tags that the ingest function adds. Parquet, a folder a day: a query reads only the
        # columns it uses, which keeps a page cheap and fast as the months add up.
        telemetry = self.table("TelemetryTable", database, "telemetry", TELEMETRY_COLUMNS,
                               f"s3://{data.bucket_name}/telemetry", partition="dt", partition_format="yyyy-MM-dd",
                               folder="dt=${dt}")
        # The bill: the columns infra/cur_export.py asks Data Exports for, in Parquet, a folder a month
        self.table("CurTable", database, "cur", CUR_COLUMNS,
                   f"s3://{data.bucket_name}/cur/{cur_export(self.config)}/data",
                   partition="billing_period", partition_format="yyyy-MM", folder="BILLING_PERIOD=${billing_period}")

        # Firehose turns the ingest function's JSON rows into Parquet, with the telemetry table as the schema
        firehose_role = iam.Role(self, "FirehoseRole", assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"))
        data.grant_write(firehose_role, "telemetry*")
        firehose_role.add_to_policy(iam.PolicyStatement(
            actions=["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"], resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/{database}",
                f"arn:aws:glue:{self.region}:{self.account}:table/{database}/telemetry"]))
        fh = firehose.CfnDeliveryStream
        stream = fh(
            self, "Telemetry", delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=fh.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=data.bucket_arn, role_arn=firehose_role.role_arn,
                compression_format="UNCOMPRESSED",  # Parquet compresses itself
                prefix="telemetry/dt=!{timestamp:yyyy-MM-dd}/",
                error_output_prefix="telemetry-errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                # A file every 5 minutes at most: fewer, larger files keep Athena fast over the months, and a
                # budget can afford to be 5 minutes behind
                buffering_hints=fh.BufferingHintsProperty(interval_in_seconds=300, size_in_m_bs=64),
                data_format_conversion_configuration=fh.DataFormatConversionConfigurationProperty(
                    enabled=True,
                    input_format_configuration=fh.InputFormatConfigurationProperty(
                        deserializer=fh.DeserializerProperty(open_x_json_ser_de=fh.OpenXJsonSerDeProperty())),
                    output_format_configuration=fh.OutputFormatConfigurationProperty(
                        serializer=fh.SerializerProperty(parquet_ser_de=fh.ParquetSerDeProperty())),
                    schema_configuration=fh.SchemaConfigurationProperty(
                        catalog_id=self.account, database_name=database, table_name="telemetry",
                        region=self.region, role_arn=firehose_role.role_arn, version_id="LATEST"))))
        stream.node.add_dependency(firehose_role)
        stream.node.add_dependency(telemetry)

        athena.CfnWorkGroup(
            self, "Workgroup", name=self.name, recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True, bytes_scanned_cutoff_per_query=10 * 1024 ** 3,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{data.bucket_name}/athena-results/")))
        return data, stream, database

    def table(self, id, database, name, columns, location, *, partition, partition_format, folder):
        """A Glue table over Parquet, with partition projection: Athena works out the partitions from the dates, so
        there are no crawlers to run and no partitions to add."""
        monthly = partition_format == "yyyy-MM"
        table = glue.CfnTable.TableInputProperty(
            name=name, table_type="EXTERNAL_TABLE",
            partition_keys=[glue.CfnTable.ColumnProperty(name=partition, type="string")],
            parameters={"projection.enabled": "true",
                        f"projection.{partition}.type": "date",
                        f"projection.{partition}.format": partition_format,
                        f"projection.{partition}.range": ("2025-01" if monthly else "2025-01-01") + ",NOW",
                        f"projection.{partition}.interval": "1",
                        f"projection.{partition}.interval.unit": "MONTHS" if monthly else "DAYS",
                        "storage.location.template": f"{location}/{folder}/"},
            storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                location=f"{location}/",
                input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                serde_info=glue.CfnTable.SerdeInfoProperty(
                    serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"),
                columns=[glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in columns.items()]))
        created = glue.CfnTable(self, id, catalog_id=self.account, database_name=database, table_input=table)
        created.node.add_dependency(self.database)  # it names the database only by name: wait for it
        return created

    def functions(self, data, stream, database, controls):
        oidc = self.config["oidc"]
        # One asset for all three: the functions and the client files the api function hands out
        code = lambda_.Code.from_asset(ROOT, exclude=["*", "!functions", "!functions/*.py", "!client", "!client/*"])
        roles = ",".join(telemetry_roles(self.config))
        queries = {"DATABASE": database, "WORKGROUP": self.name, "BUCKET": data.bucket_name, "TELEMETRY_ROLES": roles}

        def function(id: str, handler: str, timeout: int, environment: dict) -> lambda_.Function:
            return lambda_.Function(
                self, id, code=code, handler=handler, runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64, memory_size=512, timeout=cdk.Duration.seconds(timeout),
                environment=environment, log_group=logs.LogGroup(self, f"{id}Logs",
                                                                 retention=logs.RetentionDays.ONE_MONTH,
                                                                 removal_policy=cdk.RemovalPolicy.DESTROY))

        ingest = function("Ingest", "functions/ingest.handler", 10,
                          {"STREAM": stream.ref, "ACCOUNT": self.account, "TELEMETRY_ROLES": roles,
                           "BUCKET": data.bucket_name})
        data.grant_read(ingest, "config/tags.json")  # the tags the admins require, for Claude Code's hook
        ingest.add_to_role_policy(iam.PolicyStatement(actions=["firehose:PutRecordBatch"],
                                                      resources=[stream.attr_arn]))

        enforcer = function("Enforcer", "functions/enforcer.handler", 120,
                            {**queries, "OVER_BUDGET_POLICY": controls[0].managed_policy_arn,
                             "MODELS_OFF_POLICY": controls[1].managed_policy_arn})
        api_fn = function("ApiFunction", "functions/api.handler", 29, {
            **queries, "URL_PARAMETER": f"/{self.name}/url", "ENFORCER": enforcer.function_name,
            "USER_CLAIM": oidc["user_claim"], "ROLES_CLAIM": oidc["roles_claim"],
            "ADMIN_VALUES": ",".join(oidc["admin"]), "USER_VALUES": ",".join(oidc["user"]),
            "INSTALLER_VALUES": json.dumps(installer_values(self.config, self.account, self.region))})

        for fn in (api_fn, enforcer):
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"],
                resources=[f"arn:aws:athena:{self.region}:{self.account}:workgroup/{self.name}"]))
            fn.add_to_role_policy(iam.PolicyStatement(actions=["glue:GetDatabase", "glue:GetTable"], resources=[
                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                f"arn:aws:glue:{self.region}:{self.account}:database/{database}",
                f"arn:aws:glue:{self.region}:{self.account}:table/{database}/*"]))
            data.grant_read(fn)
            data.grant_read_write(fn, "athena-results/*")
        data.grant_put(api_fn, "config/*")  # the budgets, the models turned off and the tags required
        data.grant_put(enforcer, "state/*")  # where everyone stands
        api_fn.add_to_role_policy(iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[
            f"arn:aws:ssm:{self.region}:{self.account}:parameter/{self.name}/url"]))
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile"], resources=["*"]))
        enforcer.grant_invoke(api_fn)  # an admin's change applies at once, not at the next check
        enforcer.add_to_role_policy(iam.PolicyStatement(
            actions=["iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions", "iam:CreatePolicyVersion",
                     "iam:DeletePolicyVersion"], resources=[policy.managed_policy_arn for policy in controls]))
        events.Rule(self, "EnforceBudgets", schedule=events.Schedule.rate(cdk.Duration.minutes(10)),
                    targets=[targets.LambdaFunction(enforcer)])
        return ingest, api_fn

    def page_headers(self) -> cloudfront.ResponseHeadersPolicy:
        """The usual security headers, and a content security policy: the page runs only its own script, and talks
        only to its own API and the identity provider (whose token endpoint can be on another host, hence https:)."""
        policy = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                  "connect-src 'self' https:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        headers = cloudfront.ResponseSecurityHeadersBehavior(
            content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(content_security_policy=policy,
                                                                                    override=True),
            content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
            frame_options=cloudfront.ResponseHeadersFrameOptions(frame_option=cloudfront.HeadersFrameOption.DENY,
                                                                 override=True),
            referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override=True),
            strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                access_control_max_age=cdk.Duration.days(365), include_subdomains=True, override=True))
        return cloudfront.ResponseHeadersPolicy(self, "PageHeaders", security_headers_behavior=headers)

    def site(self, ingest, api_fn, certificate):
        oidc, domain = self.config["oidc"], self.config.get("domain")

        api = apigw.HttpApi(self, "Api")
        # Telemetry needs no token: the ingest function checks who sent it. It's open to the internet, so cap how
        # hard it can be hit. Firehose's default 1 MiB/s is the tighter limit (docs/reference.md: Capacity).
        api.default_stage.node.default_child.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
            throttling_rate_limit=500, throttling_burst_limit=1000)
        to_ingest = integrations.HttpLambdaIntegration("IngestIntegration", ingest)
        api.add_routes(path="/v1/{signal}", methods=[apigw.HttpMethod.POST], integration=to_ingest)
        api.add_routes(path="/v1/tags", methods=[apigw.HttpMethod.GET], integration=to_ingest)
        api.add_routes(path="/api/{proxy+}", methods=[apigw.HttpMethod.GET, apigw.HttpMethod.PUT],
                       integration=integrations.HttpLambdaIntegration("ApiIntegration", api_fn),
                       authorizer=authorizers.HttpJwtAuthorizer(
                           "Oidc", oidc["issuer"], jwt_audience=[oidc.get("audience") or oidc["client_id"]]))

        # One name for everything: the page, its API and the telemetry endpoint
        page = s3.Bucket(self, "Site", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                         removal_policy=cdk.RemovalPolicy.DESTROY, auto_delete_objects=True)
        to_api = cloudfront.BehaviorOptions(
            origin=origins.HttpOrigin(f"{api.api_id}.execute-api.{self.region}.amazonaws.com"),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL, cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER)
        distribution = cloudfront.Distribution(
            self, "Distribution", default_root_object="index.html", price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(page),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=self.page_headers()),
            additional_behaviors={"/api/*": to_api, "/v1/*": to_api},
            domain_names=[domain["name"]] if domain else None, certificate=certificate)
        url = f"https://{domain['name'] if domain else distribution.domain_name}"

        if domain:
            zone = hosted_zone(self, domain)
            alias = route53.RecordTarget.from_alias(route53_targets.CloudFrontTarget(distribution))
            route53.ARecord(self, "Alias", zone=zone, record_name=domain["name"], target=alias)
            route53.AaaaRecord(self, "AliasV6", zone=zone, record_name=domain["name"], target=alias)

        # The api function reads the URL when it runs: as an environment variable it would be a dependency cycle
        ssm.StringParameter(self, "UrlParameter", parameter_name=f"/{self.name}/url", string_value=url)
        s3deploy.BucketDeployment(
            self, "SiteContent", destination_bucket=page, distribution=distribution,
            sources=[s3deploy.Source.asset(os.path.join(ROOT, "web")), s3deploy.Source.json_data("config.json", {
                "issuer": oidc["issuer"], "clientId": oidc["client_id"], "scopes": oidc.get("scopes", "openid email"),
                "token": oidc.get("token", "access")})],
            cache_control=[s3deploy.CacheControl.from_string("no-cache")])  # the page is checked on every visit

        cdk.CfnOutput(self, "Url", value=url)
        cdk.CfnOutput(self, "RedirectUri", value=f"{url}/", description="Register this in your identity provider")
        cdk.CfnOutput(self, "DataBucket", value=self.data.bucket_name)
