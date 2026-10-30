"""Unit tests, no AWS needed: python -m unittest discover -s tests (or make test).

The fixtures are real OTLP/JSON payloads Claude Code 2.1.312 sent on Bedrock.
"""

import base64
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "functions"))
os.environ.update({"AWS_DEFAULT_REGION": "eu-north-1", "AWS_REGION": "eu-north-1", "ACCOUNT": "111122223333",
                   "WORKGROUP": "test", "DATABASE": "test",
                   "STREAM": "telemetry",
                   "MODEL_OPUS": "eu.anthropic.claude-opus-5-5", "MODEL_SONNET": "eu.anthropic.claude-sonnet-5-5",
                   "MODEL_HAIKU": "eu.anthropic.claude-haiku-5-5",
                   "TELEMETRY_ROLES": "AWSReservedSSO_ClaudeCode_*", "USER_CLAIM": "email",
                   "ROLES_CLAIM": "resource_access.claude-code.roles", "ADMIN_VALUES": "admin",
                   "USER_VALUES": "user,bedrock"})

import api  # noqa: E402
import athena  # noqa: E402
import client_files  # noqa: E402
import enforcer  # noqa: E402
import ingest  # noqa: E402
import models  # noqa: E402

spec = importlib.util.spec_from_file_location("helper", os.path.join(ROOT, "client", "otel-helper.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

ROLE = "arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_ClaudeCode_0123abcd"


def read(path: str) -> str:
    with open(path) as f:
        return f.read()


def write(path: str, text: str, executable: bool = False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if executable:
        os.chmod(path, 0o755)


def fixture(name: str) -> dict:
    return json.loads(read(os.path.join(ROOT, "tests", "fixtures", name)))


class Ingest(unittest.TestCase):
    def test_metrics_become_rows_with_the_verified_user(self):
        rows = ingest.flatten(fixture("metrics-1.json"), "alice@example.com", {"project": "apollo"})
        [cost] = [r for r in rows if r["name"] == "claude_code.cost.usage"]
        self.assertEqual(cost["user"], "alice@example.com")
        self.assertEqual(cost["tags"], {"project": "apollo"})
        self.assertEqual(cost["model"], "eu.anthropic.claude-haiku-5-5")
        self.assertAlmostEqual(cost["value"], 0.002406665)
        self.assertEqual(cost["attrs"]["service.version"], "2.1.312")  # resource attributes come along
        self.assertGreater(cost["ts"], 1_700_000_000_000)

    def test_events_are_named_without_prefix(self):
        rows = ingest.flatten(fixture("logs-1.json"), "a", {}) + ingest.flatten(fixture("logs-2.json"), "a", {})
        self.assertTrue({"user_prompt", "api_request", "tool_result"} <= {r["name"] for r in rows})
        request = next(r for r in rows if r["name"] == "api_request")
        self.assertEqual(request["kind"], "event")
        self.assertIsNone(request["value"])
        self.assertIn("cost_usd", request["attrs"])

    def test_only_sts_urls_are_fetched(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            for url in ["https://evil.example.com/?Action=GetCallerIdentity",
                        "http://sts.amazonaws.com/?Action=GetCallerIdentity",
                        "https://sts.amazonaws.com.evil.com/?Action=GetCallerIdentity",
                        "https://sts.eu-north-1.amazonaws.com/?Action=AssumeRole", "",
                        # an S3 bucket called "sts", which anyone could own, at its virtual-hosted addresses
                        "https://sts.s3.amazonaws.com/x.json?Action=GetCallerIdentity",
                        "https://sts.s3-external-1.amazonaws.com/x.json?Action=GetCallerIdentity",
                        "https://sts.us-east-1.amazonaws.com/?Action=GetCallerIdentity"]:  # not this stack's region
                self.assertIsNone(ingest.identity(url), url)
            urlopen.assert_not_called()

    def test_identity_center_role_of_this_account_only(self):
        self.assertEqual(ingest.user_from_arn(f"{ROLE}/Alice@Example.com"), "Alice@Example.com")
        for arn in [f"{ROLE}/alice".replace("111122223333", "999988887777"),
                    f"{ROLE}/alice".replace("ClaudeCode", "Admin"),
                    "arn:aws:iam::111122223333:user/alice"]:
            self.assertIsNone(ingest.user_from_arn(arn), arn)

    def test_tags_are_checked(self):
        sent = {"project": "apollo", "team": "payments", "Bad Key": "x", "note": "<script>", "n": 1}
        event = {"headers": {"x-cc-identity": "url", "x-cc-tags": json.dumps(sent)},
                 "body": json.dumps(fixture("metrics-1.json"))}
        with mock.patch.object(ingest, "identity", return_value="alice"), \
                mock.patch.object(ingest, "put", return_value=0) as put:
            self.assertEqual(ingest.handler(event, None)["statusCode"], 200)
        self.assertTrue(all(r["tags"] == {"project": "apollo", "team": "payments"} for r in put.call_args[0][0]))
        for header in ["", "not json", "[1, 2]"]:
            self.assertEqual(ingest.parse_tags(header), {})
        self.assertEqual(len(ingest.parse_tags(json.dumps({f"k{i}": "v" for i in range(50)}))), ingest.MAX_TAGS)

    def test_only_a_request_stored_not_at_all_is_a_503(self):
        refused = {"FailedPutCount": 1, "RequestResponses": [{"ErrorCode": "ServiceUnavailableException"}]}
        with mock.patch.object(ingest, "firehose") as firehose, mock.patch.object(ingest.time, "sleep"):
            firehose.put_record_batch.return_value = refused
            self.assertEqual(ingest.put([{"a": 1}]), 1)
            self.assertEqual(firehose.put_record_batch.call_count, 3)
        event = {"headers": {"x-cc-identity": "url"}, "body": json.dumps(fixture("metrics-1.json"))}
        rows = len(ingest.flatten(fixture("metrics-1.json"), "alice", {}))
        with mock.patch.object(ingest, "identity", return_value="alice"):
            with mock.patch.object(ingest, "put", return_value=rows):  # nothing stored: Claude Code sends it again
                self.assertEqual(ingest.handler(event, None)["statusCode"], 503)
            with mock.patch.object(ingest, "put", return_value=1):  # some stored: sending again would double them
                self.assertEqual(ingest.handler(event, None)["statusCode"], 200)

    def test_firehose_refusing_the_whole_call_counts_as_all_refused(self):
        refused = ingest.botocore.exceptions.ClientError(
            {"Error": {"Code": "ServiceUnavailableException", "Message": "over its limit"}}, "PutRecordBatch")
        with mock.patch.object(ingest.firehose, "put_record_batch", side_effect=refused) as call, \
                mock.patch.object(ingest.time, "sleep"):
            self.assertEqual(ingest.put([{"a": 1}, {"b": 2}]), 2)
        self.assertEqual(call.call_count, 3)

    def test_malformed_identity_headers_are_refused_not_crashing(self):
        for url in ["https://[", "https://sts.amazonaws.com/?Action=GetCallerIdentity&a=b c",
                    "https://evil\uff03.com", "https://\x7f"]:
            self.assertIsNone(ingest.identity(url), url)

    def test_unverified_is_refused(self):
        self.assertEqual(ingest.handler({"headers": {}, "body": "{}"}, None)["statusCode"], 401)


class Helper(unittest.TestCase):
    def test_presigned_url_shape(self):
        helper.REGION = "eu-north-1"
        creds = {"AccessKeyId": "AKIDEXAMPLE", "SecretAccessKey": "secret", "SessionToken": "tok/en+="}
        url = helper.presigned_identity_url(creds, datetime.datetime(2026, 10, 28, 12, tzinfo=datetime.UTC))
        self.assertTrue(url.startswith("https://sts.eu-north-1.amazonaws.com/?Action=GetCallerIdentity&"))
        self.assertIn("X-Amz-Security-Token=tok%2Fen%2B%3D", url)
        self.assertRegex(url, r"X-Amz-Signature=[0-9a-f]{64}$")

    def test_cc_variables_become_tags(self):
        with mock.patch.dict(os.environ, {"CC_PROJECT": "zeus", "CC_COST_CENTER": "4711", "CC_EMPTY": "", "CCX": "no"},
                             clear=True):
            self.assertEqual(helper.tags(), {"project": "zeus", "cost_center": "4711"})


class Api(unittest.TestCase):
    def person(self, claims: dict):
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return api.person({"headers": {"authorization": f"Bearer header.{payload}.signature"}})

    def test_keycloak_client_roles(self):
        me = self.person({"email": "a@x", "email_verified": True,
                          "resource_access": {"claude-code": {"roles": ["admin"]}}})
        self.assertEqual(me, {"user": "a@x", "admin": True})

    def test_user_only_and_nobody(self):
        def roles(*values):
            return {"resource_access": {"claude-code": {"roles": list(values)}}}
        self.assertEqual(self.person({"email": "b@x", "email_verified": True, **roles("user")})["admin"], False)
        self.assertIsNone(self.person({"email": "c@x", **roles("other")}))
        self.assertIsNone(self.person({"email": "c@x"}))

    def test_flat_and_dotted_claims(self):
        self.assertEqual(api.claim({"cognito:groups": ["admin"]}, "cognito:groups"), ["admin"])
        self.assertEqual(api.claim({"https://x.com/roles": ["a"]}, "https://x.com/roles"), ["a"])
        self.assertEqual(api.claim({"roles": "admin"}, "roles"), "admin")

    def test_non_admin_is_always_scoped_to_themselves(self):
        query = {"from": "2026-10-01", "to": "2026-10-28", "user": "a@x"}
        _, params, user = api.scope({"user": "b@x", "admin": False}, query)
        self.assertEqual(user, "b@x")
        self.assertIn("b@x", params)
        self.assertNotIn("a@x", params)

    def test_dates_are_checked(self):
        with self.assertRaises(ValueError):
            api.scope({"user": "a", "admin": True}, {"from": "2026-10-01'; DROP", "to": "2026-10-02"})

    def test_any_model_or_routing_can_be_off_but_the_default_must_be_offered(self):
        with mock.patch.object(api, "s3"), mock.patch.object(api, "enforce"), mock.patch.object(api, "model_list"), \
                mock.patch.object(models, "available", return_value=Models.AVAILABLE), \
                mock.patch.object(api, "config_models", return_value=Models.CONFIG):
            # config.yaml's models too
            api.save_model_list({"disabled": ["anthropic.claude-haiku-5-5"], "routes_off": ["eu"]})
            with self.assertRaises(ValueError):
                api.save_model_list({"disabled": ["arn:aws:iam::*"]})
            with self.assertRaises(ValueError):  # the default must be allowed
                api.save_model_list({"disabled": ["anthropic.claude-fable-5"], "default": "anthropic.claude-fable-5"})
            with self.assertRaises(ValueError):  # and reachable: Fable is global. only
                api.save_model_list({"default": "anthropic.claude-fable-5-1", "routes_off": ["global"]})
            api.save_model_list({"disabled": ["anthropic.claude-fable-5"], "default": "anthropic.claude-opus-5-5"})

    def test_metric_totals(self):
        self.assertEqual(api.total("token.usage", "input", "cacheRead"),
                         "sum(if(name = 'claude_code.token.usage' AND type IN ('input', 'cacheRead'), value))")

    def test_unverified_email_is_refused(self):
        claims = {"email": "a@x", "resource_access": {"claude-code": {"roles": ["admin"]}}}
        self.assertIsNone(self.person({**claims, "email_verified": False}))
        self.assertIsNone(self.person({k: v for k, v in claims.items()}))

    def test_budget_names_are_user_names(self):
        for name in ["*", "a*", "?", "x y"]:
            with self.assertRaises(ValueError), mock.patch.object(api, "s3"):
                api.save_budgets({"users": {name: {"daily": 1}}})


class Models(unittest.TestCase):
    AVAILABLE = {"anthropic.claude-opus-5-5": ["eu.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5"],
                 "anthropic.claude-opus-4-6-v1": ["eu.anthropic.claude-opus-4-6-v1",
                                                  "global.anthropic.claude-opus-4-6-v1"],
                 "anthropic.claude-sonnet-5-5": ["eu.anthropic.claude-sonnet-5-5"],
                 "anthropic.claude-haiku-5-5": ["eu.anthropic.claude-haiku-5-5"],
                 "anthropic.claude-fable-5-1": ["global.anthropic.claude-fable-5-1"]}
    CONFIGURED = {"opus": "eu.anthropic.claude-opus-5-5", "sonnet": "eu.anthropic.claude-sonnet-5-5[1m]",
                  "haiku": "eu.anthropic.claude-haiku-5-5"}
    CONFIG = models.from_config(CONFIGURED, None)

    def test_with_eu_off_config_models_go_global(self):
        picks = models.choices(self.AVAILABLE, {"routes_off": ["eu"]}, self.CONFIG)
        self.assertTrue(picks["options"])
        self.assertTrue(all(o["model"].startswith("global.") for o in picks["options"]), picks)

    def test_an_application_profile_counts_as_its_model(self):
        arn = "arn:aws:bedrock:eu-north-1:111122223333:application-inference-profile/abc123"
        bedrock = mock.Mock()
        bedrock.get_inference_profile.return_value = {"models": [
            {"modelArn": "arn:aws:bedrock:eu-north-1::foundation-model/anthropic.claude-opus-5-5"}]}
        config = models.from_config({**self.CONFIGURED, "opus": arn}, bedrock)
        self.assertEqual(config["anthropic.claude-opus-5-5"], arn)
        picks = models.choices(self.AVAILABLE, {}, config)
        self.assertIn({"model": arn, "label": "Opus 5.5"}, picks["options"])  # the profile, not around it

    def test_model_names(self):
        for model_id, name in [("eu.anthropic.claude-opus-5-5", "Opus 5.5"), ("claude-haiku-5-5", "Haiku 5.5"),
                               ("eu.anthropic.claude-opus-4-6-v1[1m]", "Opus 4.6"),
                               ("global.anthropic.claude-sonnet-4-5-20250929-v1:0[1m]", "Sonnet 4.5"),
                               ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", "Sonnet 4.5"),
                               ("eu.anthropic.claude-opus-4-6-v1", "Opus 4.6"),
                               ("global.anthropic.claude-fable-5", "Fable 5")]:
            self.assertEqual(models.model_name(model_id), name, model_id)

    def test_one_model_whatever_its_region_or_window(self):
        self.assertEqual(models.base_model("eu.anthropic.claude-opus-4-6-v1[1m]"), "anthropic.claude-opus-4-6-v1")
        self.assertEqual(models.base_model("global.anthropic.claude-opus-5-5"), "anthropic.claude-opus-5-5")

    def test_picker_rows_are_the_allowed_models(self):
        picks = models.choices(self.AVAILABLE, {"disabled": ["anthropic.claude-opus-4-6-v1"],
                                                "default": "anthropic.claude-fable-5-1"}, self.CONFIG)
        self.assertEqual(picks["options"], [
            {"model": "eu.anthropic.claude-opus-5-5", "label": "Opus 5.5"},
            {"model": "eu.anthropic.claude-sonnet-5-5[1m]", "label": "Sonnet 5.5"},  # config's ID, as it is
            {"model": "eu.anthropic.claude-haiku-5-5", "label": "Haiku 5.5"},
            {"model": "global.anthropic.claude-fable-5-1", "label": "Fable 5.1"}])  # no eu. profile: global.
        self.assertEqual(picks["default"], "global.anthropic.claude-fable-5-1")

    def test_a_routing_turned_off_leaves_its_models_out(self):
        picks = models.choices(self.AVAILABLE, {"default": "anthropic.claude-fable-5-1", "routes_off": ["global"]},
                               self.CONFIG)
        labels = [row["label"] for row in picks["options"]]
        self.assertNotIn("Fable 5.1", labels)  # only through global.
        self.assertTrue(all(row["model"].startswith("eu.") for row in picks["options"]))
        self.assertIsNone(picks["default"])

    def test_newest_first_and_no_default_unless_chosen(self):
        picks = models.choices(self.AVAILABLE, {}, self.CONFIG)
        labels = [row["label"] for row in picks["options"]]
        self.assertLess(labels.index("Opus 5.5"), labels.index("Opus 4.6"))
        self.assertLess(models.order("anthropic.claude-opus-5-5"), models.order("anthropic.claude-opus-5"))
        self.assertIsNone(picks["default"])


class Athena(unittest.TestCase):
    def test_busy_is_retried_and_a_failure_stops_the_rest(self):
        busy = athena.athena.exceptions.TooManyRequestsException(
            {"Error": {"Code": "TooManyRequestsException", "Message": "busy"}}, "StartQueryExecution")
        with mock.patch.object(athena.athena, "start_query_execution",
                               side_effect=[busy, {"QueryExecutionId": "a"}, {"QueryExecutionId": "b"}]), \
                mock.patch.object(athena.athena, "get_query_execution", side_effect=lambda QueryExecutionId: {
                    "QueryExecution": {"Status": {"State": "FAILED" if QueryExecutionId == "b" else "RUNNING",
                                                  "StateChangeReason": "bad"}}}), \
                mock.patch.object(athena.athena, "stop_query_execution") as stop, \
                mock.patch.object(athena.time, "sleep"):
            with self.assertRaises(RuntimeError):
                athena.run({"one": ("SELECT 1", []), "two": ("SELECT 2", [])})
        self.assertEqual({c.kwargs["QueryExecutionId"] for c in stop.call_args_list}, {"a", "b"})


class Enforcer(unittest.TestCase):
    def test_mixed_case_names_are_blocked(self):
        spend = {"alice@example.com": {"names": ["Alice@Example.com"], "daily": 5.0, "weekly": 5.0, "monthly": 5.0}}
        budgets = {"default": {"daily": 1}, "users": {"alice@example.com": {"monthly": 100}}}
        for _ in range(20):  # whatever order the sets come in
            users, blocked = enforcer.assess(budgets, spend)
            self.assertEqual(blocked, ["Alice@Example.com"])
            self.assertEqual(users["alice@example.com"]["over"], ["daily"])

    def test_limit_of_zero_blocks_before_any_spend(self):
        _, blocked = enforcer.assess({"default": {}, "users": {"bob@example.com": {"daily": 0}}}, {})
        self.assertEqual(blocked, ["bob@example.com"])

    def test_reset_forgives_until_the_period_ends(self):
        today = datetime.date(2026, 10, 29)
        starts = {"daily": today, "weekly": datetime.date(2026, 10, 26), "monthly": datetime.date(2026, 10, 1)}
        spend = {"alice@example.com": {"names": ["alice@example.com"], "daily": 5.0, "weekly": 8.0, "monthly": 20.0}}
        forgiven = enforcer.reset({}, spend, starts, "*")
        later = {"alice@example.com": {**spend["alice@example.com"], "daily": 6.0}}  # one more dollar since
        self.assertEqual(enforcer.forgive(later, forgiven, starts)["alice@example.com"]["daily"], 1.0)
        tomorrow = {**starts, "daily": datetime.date(2026, 10, 30)}  # a new day: nothing left to forgive
        self.assertEqual(enforcer.forgive(later, forgiven, tomorrow)["alice@example.com"]["daily"], 6.0)
        self.assertEqual(enforcer.forgive(later, forgiven, tomorrow)["alice@example.com"]["weekly"], 0.0)

    def test_models_turned_off_are_denied_everywhere(self):
        [models] = enforcer.models_off(["anthropic.claude-fable-5", "not a model"])["Statement"]
        self.assertEqual(models["Resource"], ["arn:aws:bedrock:*:*:inference-profile/*.anthropic.claude-fable-5",
                                              "arn:aws:bedrock:*::foundation-model/anthropic.claude-fable-5"])
        [routes] = enforcer.models_off([], ["global", "not a route!"])["Statement"]
        self.assertEqual(routes["Resource"], ["arn:aws:bedrock:*:*:inference-profile/global.*"])
        [none] = enforcer.models_off([])["Statement"]
        self.assertEqual(none["Resource"], [enforcer.NO_MODEL])

    def test_a_full_policy_keeps_those_furthest_over(self):
        blocked = [f"person.number.{i:03}@example-company.com" for i in range(300)]
        document, left_out = enforcer.fit(blocked)
        kept = document["Statement"][0]["Condition"]["StringLike"]["aws:userid"]
        self.assertLessEqual(len(json.dumps(document, separators=(",", ":"))), 6144)
        self.assertEqual(kept, sorted(f"*:{n}" for n in blocked[:len(kept)]))  # the first ones: furthest over
        self.assertEqual(left_out, blocked[len(kept):])

    def test_two_resets_close_together_both_hold(self):
        store = {"state/forgiven.json": {}}
        spent = {"names": [], "daily": 5.0, "weekly": 5.0, "monthly": 5.0}

        def spending(starts, today):  # meanwhile, another reset saves bob's forgiveness
            store["state/forgiven.json"] = {"bob@x.com": {p: [starts[p].isoformat(), 5.0] for p in enforcer.PERIODS}}
            return {"alice@x.com": {**spent, "names": ["alice@x.com"]}, "bob@x.com": {**spent, "names": ["bob@x.com"]}}

        with mock.patch.object(enforcer, "load", side_effect=lambda key: store.get(key)), \
                mock.patch.object(enforcer, "save", side_effect=lambda key, data: store.__setitem__(key, data)), \
                mock.patch.object(enforcer, "spending", side_effect=spending), \
                mock.patch.object(enforcer, "apply"):
            store["config/budgets.json"] = {"default": {"daily": 1}, "users": {}}
            enforcer.handler({"reset": "alice@x.com"}, None)
        self.assertEqual(set(store["state/forgiven.json"]), {"alice@x.com", "bob@x.com"})
        self.assertEqual(store["state/budgets.json"]["blocked"], [])

    def test_models_apply_even_when_spend_cant_be_read(self):
        with mock.patch.object(enforcer, "load", return_value=None), \
                mock.patch.object(enforcer, "spending", side_effect=RuntimeError("Athena")), \
                mock.patch.object(enforcer, "apply") as apply:
            with self.assertRaises(RuntimeError):
                enforcer.handler({}, None)
        self.assertEqual(apply.call_args_list[0].args[0], enforcer.MODELS_OFF_POLICY)

    def test_ids_must_match_whole(self):
        self.assertIsNone(models.MODEL_ID.fullmatch("anthropic.x\n"))
        self.assertIsNone(models.ROUTE_ID.fullmatch("eu\n"))
        self.assertIsNone(enforcer.people.NAME.fullmatch("a\n"))

    def test_a_new_order_alone_doesnt_change_the_policy(self):
        self.assertEqual(enforcer.fit(["b@x.com", "a@x.com"])[0], enforcer.fit(["a@x.com", "b@x.com"])[0])

    def test_policy_denies_by_session_name(self):
        [statement] = enforcer.over_budget(["alice@example.com"])["Statement"]
        self.assertEqual(statement["Effect"], "Deny")
        self.assertEqual(statement["Condition"]["StringLike"]["aws:userid"], ["*:alice@example.com"])
        [nobody] = enforcer.over_budget([])["Statement"]
        self.assertEqual(nobody["Condition"]["StringLike"]["aws:userid"], [enforcer.NOBODY])


# Stands in for the AWS CLI: its version, and credentials unless SIGNED_OUT is set. Nothing reaches AWS.
AWS_STAND_IN = """#!/bin/sh
case "$1" in
  configure) [ -n "$SIGNED_OUT" ] && exit 255
             echo '{"Version": 1, "AccessKeyId": "AKIDEXAMPLE", "SecretAccessKey": "secret", "SessionToken": "t"}' ;;
  *) echo aws-cli/2.30.0 ;;
esac
"""


class Client(unittest.TestCase):
    VALUES = {"NAME": "claude-code", "START_URL": "https://d-1.awsapps.com/start", "SSO_REGION": "eu-west-1",
              "ACCOUNT": "111122223333", "PERMISSION_SET": "ClaudeCode", "REGION": "eu-north-1",
              "URL": "https://claude.example.com", "MODEL_OPUS": "eu.anthropic.claude-opus-5-5",
              "MODEL_SONNET": "eu.anthropic.claude-sonnet-5-5", "MODEL_HAIKU": "eu.anthropic.claude-haiku-5-5"}

    PICKS = {"options": [{"model": "eu.anthropic.claude-opus-5-5", "label": "Opus 5.5"},
                         {"model": "eu.anthropic.claude-sonnet-5-5", "label": "Sonnet 5.5"}],
             "default": "eu.anthropic.claude-sonnet-5-5"}

    def test_installer_is_filled_in_and_merges(self):
        script = client_files.install_script(self.VALUES, self.PICKS)
        self.assertNotRegex(script, r"@[A-Z_]+@")
        with tempfile.TemporaryDirectory() as home:
            write(f"{home}/.aws/config", "[default]\nregion = us-east-1\n\n[profile claude-code]\nold = 1\n")
            write(f"{home}/.claude/settings.json", json.dumps({"theme": "dark", "env": {"KEEP": "1"}}))
            write(f"{home}/install.sh", script)
            # Stand-ins, so nothing is downloaded and no AWS is called
            write(f"{home}/bin/aws", AWS_STAND_IN, executable=True)
            write(f"{home}/bin/claude", "#!/bin/sh\n", executable=True)
            env = {**os.environ, "HOME": home, "PATH": f"{home}/bin:/usr/bin:/bin"}
            for _ in range(2):  # running it again changes nothing more
                subprocess.run(["bash", f"{home}/install.sh", "--no-login"],
                               env=env, check=True, capture_output=True)

            config = read(f"{home}/.aws/config")
            self.assertIn("[default]\nregion = us-east-1", config)
            self.assertEqual(config.count("[profile claude-code]"), 1)
            self.assertNotIn("old = 1", config)
            self.assertIn("sso_start_url = https://d-1.awsapps.com/start", config)
            self.assertIn("sso_role_name = ClaudeCode", config)

            settings = json.loads(read(f"{home}/.claude/settings.json"))
            self.assertEqual(settings["theme"], "dark")
            self.assertEqual(settings["env"]["KEEP"], "1")
            self.assertEqual(settings["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"], "https://claude.example.com")
            self.assertNotIn("AWS_PROFILE", settings["env"])
            self.assertEqual(settings["modelPicker"], {"options": self.PICKS["options"], "replaceBuiltInOptions": True})
            self.assertEqual(settings["env"]["ANTHROPIC_DEFAULT_MODEL"], "eu.anthropic.claude-sonnet-5-5")
            self.assertEqual(settings["awsCredentialExport"],
                             f"{home}/bin/aws configure export-credentials --profile claude-code --format process")

            out = subprocess.run([f"{home}/.claude/cc-bedrock/otel-helper"], env={**env, "CC_PROJECT": "apollo"},
                                 capture_output=True, text=True)
            headers = json.loads(out.stdout)
            self.assertEqual(json.loads(headers["x-cc-tags"]), {"project": "apollo"})
            self.assertTrue(headers["x-cc-identity"].startswith("https://sts.eu-north-1.amazonaws.com/?"))
            # Signed out: it fails, so Claude Code says telemetry isn't being sent
            signed_out = subprocess.run([f"{home}/.claude/cc-bedrock/otel-helper"], env={**env, "SIGNED_OUT": "1"},
                                        capture_output=True, text=True)
            self.assertEqual(signed_out.returncode, 1)
            self.assertIn("aws sso login", signed_out.stderr)

    def test_reinstalling_takes_away_what_the_admins_took_away(self):
        with tempfile.TemporaryDirectory() as home:
            write(f"{home}/bin/aws", AWS_STAND_IN, executable=True)
            write(f"{home}/bin/claude", "#!/bin/sh\n", executable=True)
            env = {**os.environ, "HOME": home, "PATH": f"{home}/bin:/usr/bin:/bin"}
            for picks in (self.PICKS, {"options": [], "default": None}):
                write(f"{home}/install.sh", client_files.install_script(self.VALUES, picks))
                subprocess.run(["bash", f"{home}/install.sh", "--no-login"], env=env, check=True, capture_output=True)
            settings = json.loads(read(f"{home}/.claude/settings.json"))
            self.assertNotIn("modelPicker", settings)
            self.assertNotIn("ANTHROPIC_DEFAULT_MODEL", settings["env"])

    def test_odd_settings_files(self):
        with tempfile.TemporaryDirectory() as home:
            write(f"{home}/bin/aws", AWS_STAND_IN, executable=True)
            write(f"{home}/bin/claude", "#!/bin/sh\n", executable=True)
            write(f"{home}/install.sh", client_files.install_script(self.VALUES, self.PICKS))
            env = {**os.environ, "HOME": home, "PATH": f"{home}/bin:/usr/bin:/bin"}
            for text in ["", '{"env": null, "theme": "dark"}']:
                write(f"{home}/.claude/settings.json", text)
                subprocess.run(["bash", f"{home}/install.sh", "--no-login"], env=env, check=True, capture_output=True)
                settings = json.loads(read(f"{home}/.claude/settings.json"))
                self.assertEqual(settings["env"]["CLAUDE_CODE_USE_BEDROCK"], "1")
            write(f"{home}/.claude/settings.json", '{"theme": "dark",}')  # not JSON: left alone, and said so
            run = subprocess.run(["bash", f"{home}/install.sh", "--no-login"], env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("isn't valid JSON", run.stderr)
            self.assertEqual(read(f"{home}/.claude/settings.json"), '{"theme": "dark",}')

    def test_managed_settings(self):
        settings = json.loads(client_files.managed_settings(self.VALUES, self.PICKS))
        self.assertEqual(settings["env"]["CLAUDE_CODE_USE_BEDROCK"], "1")
        self.assertEqual(settings["allowedProviders"], ["bedrock"])


if __name__ == "__main__":
    unittest.main()
