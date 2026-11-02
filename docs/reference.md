# Reference

The details behind the [README](../README.md): how each part works, what it can and can't do, and what to watch.

- [Setting up](#setting-up): the permission set by hand, a domain of your own
- [On the laptop](#on-the-laptop): what the installer does, tags, MDM, what a repository can change, how telemetry
  proves who sent it
- [The dashboard](#the-dashboard)
- [Controls](#controls): models, where requests may go, required tags, budgets
- [Capacity and quotas](#capacity-and-quotas)
- [Billed cost](#billed-cost)
- [Data, privacy, cost](#data-privacy-cost)
- [What's left out, and why](#whats-left-out-and-why)
- [Taking it down](#taking-it-down)
- [Layout](#layout)

[Identity providers](identity-providers.md) has its own page.

## Setting up

Bedrock subscribes the account to a model the first time someone calls it, and the permission set allows that. If
Anthropic's models have never been used in the account, Bedrock may ask for a short use-case form in its console
first. `make deploy` takes about ten minutes the first time, and is safe to run again after any change.

`make permission-set` deploys a CloudFormation stack, `<name>-permission-set`, in the Identity Center admins'
account in `identity_center.region`: it finds the region's Identity Center instance, finds the config's groups by
name and deploys [`idc/permission-set.yaml`](../idc/permission-set.yaml).

### The permission set by hand

The same as `make permission-set`, by hand in the console: *IAM Identity Center → Permission sets → Create*, a
custom permission set named as `identity_center.permission_set`, session duration as
`identity_center.session_duration` (12 hours by default), no AWS managed policies, and these customer managed
policies: `<name>-bedrock`, `<name>-over-budget`, `<name>-models-off`. Then *AWS accounts → the Bedrock account →
Assign users or groups*, with the groups and that permission set.

### Your own domain

Without one the dashboard is on `*.cloudfront.net`. For a name of your own, `claude.example.com` say:

1. A public hosted zone for it in the Bedrock account (`aws route53 create-hosted-zone --name claude.example.com
   --caller-reference $(date +%s)`), and its four name servers as an `NS` record for `claude` in
   `example.com`'s zone, wherever that is. `dig NS claude.example.com` answers once it's delegated.
2. `domain` in the config: the name, and the zone's ID and name. `make deploy` then adds a certificate (in
   us-east-1, which CloudFront requires, validated in that zone) and points the name at CloudFront.
3. The new `RedirectUri` in the OIDC client, beside the old one until the new one works; and since telemetry goes
   to the dashboard's address, people run the installer from the new address again.

The certificate waits for the delegation, so do step 1 first: without it the deploy hangs.

## On the laptop

It installs Claude Code and the AWS CLI if they're missing, both for the person only, without admin rights: Claude
Code with Anthropic's own installer, which puts it in `~/.local/bin` anyway, and the AWS CLI with the current-user
install AWS documents, into `~/.local/aws-cli`. Then it adds an `[sso-session]` with your Identity Center portal URL
and a `[profile]` with the account and permission set to `~/.aws/config`, merges the Bedrock, telemetry and
tag-check settings into `~/.claude/settings.json` (leaving everything else in it alone, your own hooks too, apart
from a default model or `/model` list of your own, which the admins' replace, and AWS credentials of your own),
installs the telemetry helper, and, unless you're signed in already, opens Identity Center's sign-in in the
browser, which goes on to Entra, Keycloak or whatever Identity Center federates to. After that it's `claude`.
Started signed out, Claude Code opens the sign-in first and waits for it; when the sign-in runs out mid-session, it
runs `aws sso login` itself (`awsAuthRefresh`); and the CLI renews role credentials silently for as long as the
Identity Center session lasts (set the access portal's session duration to what your security team accepts, up to 90
days).

Claude Code gets its credentials from the helper (`awsCredentialExport`) rather than `AWS_PROFILE`, so that commands
Claude runs for you (`aws s3 ls`) keep using your own profiles, not the Bedrock account. Signed out, the helper signs
you in rather than fail: Claude Code would otherwise go on with whatever other AWS credentials the laptop has, in
another account and outside the budgets. For the same reason the installer takes AWS credentials of your own
(`AWS_PROFILE`, access keys, a Bedrock API key) out of Claude Code's settings; your shell and `~/.aws` keep them.

### Tags

For one session, put them on the command line: `CC_PROJECT=apollo claude`. Settings files beat the shell, so in a
repository that sets its own, override with `claude --settings '{"env": {"CC_PROJECT": "apollo"}}'`, which keeps
the repository's other tags. Without any, usage has no tags. A new session picks a change up at once, a running one
within 5 minutes (when Claude Code next runs the helper). Usage already recorded keeps its tags. Admins can
require some ([Controls](#controls)).

### MDM

`make client` writes `dist/managed-settings.json` for `/Library/Application Support/ClaudeCode/` (macOS) or
`/etc/claude-code/` (Linux) and `dist/claude-otel-helper` for `/usr/local/bin/`. Managed settings can't be
overridden, so telemetry (and with it budgets' estimate) and the tag check stay on, and they allow only Bedrock as
the provider. The installer leaves its own tag-check hooks out where managed settings carry them. They expect the
AWS CLI where its own installer puts it, `/usr/local/bin/aws`. People still need the profile in `~/.aws/config`: run
`dist/install-claude-code.sh --no-login` as them, or write those lines yourself.

### What a repository can change

Claude Code passes a repository's `.claude/settings.json` `env` to the commands it
runs, `PATH`, `HOME` and `PYTHONPATH` included. The helper doesn't let that pick its programs: it runs the Python
the installer found, isolated and without `site` (`-IS`; macOS Pythons read `PYTHONEXECUTABLE` even when isolated),
and the AWS CLI by its full path, with a clean environment and this account's real home. `dist/claude-otel-helper`
names `/usr/bin/python3`, which on macOS is Apple's stub and follows `DEVELOPER_DIR`; if you rely on
`allowManagedHooksOnly` to keep repositories from running code, change its first line to the interpreter itself.
Claude Code's own `awsAuthRefresh` command runs as Claude Code decides, and a repository you trust can run hooks of
its own anyway.

### How telemetry proves who sent it

Claude Code calls `otelHeadersHelper` at start and every 5 minutes (the installer sets that). The helper presigns an
STS `GetCallerIdentity` request with the person's Identity Center credentials and sends the URL as a header. The
ingest function calls it: STS answers with the caller's role and session name, which must be this account's Claude
Code role. That's the pattern EKS uses for IAM authentication. It means there is no shared ingest token to
distribute or rotate, nobody outside the permission set can send telemetry, and nobody can send it in someone else's
name.

## The dashboard

- **Usage**: estimated and billed spend, people, sessions, tokens and cache share, lines changed, commits, pull
  requests, active time, spend per day by model, and tables by person (estimated and billed side by side, and anyone
  billed who sends no telemetry flagged), tag, model, source and tool. Click a person or a tag to drill into them;
  the address keeps the range and filters, so a link shows someone the same.
- **Controls** (admins): which models and regions are allowed, the default model, the tags sessions must carry,
  budgets per person, where everyone stands, and resets.
- **Set up**: the installer, how to use it, and the tags the organisation asks for.

## Controls

The Controls tab's two IAM policies are `<name>-models-off` and `<name>-over-budget`, in the Bedrock account and
included in the permission set. Saving applies within a minute.

**Models.** The tab lists the Anthropic models Bedrock offers in the region. One turned off is denied for everyone,
on its inference profiles and the model itself. The allowed ones are what Claude Code's `/model` picker offers, a
row for each routing that's on (`Opus 5.5 · EU`, `Opus 5.5 · Global`; a model with one routing keeps its plain
name), and the one marked **Default** is what new sessions start on (through the routing `config.yaml` names first);
what someone picks in `/model` stays their choice until they run the installer again. The installer writes these as
Claude Code's `modelPicker` and `ANTHROPIC_DEFAULT_MODEL`, so people get a new list by downloading and running it
again; Bedrock enforces a change straight away either way. Any model can be off, `config.yaml`'s too. Claude Code
also makes small background calls (a session's title, say) with `config.yaml`'s haiku model; with that one off, the
installer points them at the nearest allowed model (Sonnet), and until people install again they fail quietly while
sessions carry on.

**Where requests may go.** Each cross-region inference profile routes within a geography (`eu.`: EU regions only) or
to any commercial region (`global.`). Turn a routing off and every profile through it is denied, for every model;
models only available that way (Fable, today, is `global.` only) drop out of `/model`, which lists the others once
for each routing that's still on, the one `config.yaml` names first. Claude Code's own opus, sonnet and haiku models
(what a session starts on without a default, what subagents use) follow the same way: through a routing that's on,
or, with the model off, the newest allowed one of the same family. An application inference profile in `config.yaml`
isn't a routing: turning a routing off doesn't stop it, so make it from a profile whose routing you mean to keep.

**Required tags.** The tags sessions should carry, `project` say, each with what happens when it's missing:
**Warn** tells people at the start of a session and how to set it; **Require** also holds their prompts back until
it's set. The installer adds two Claude Code hooks (`SessionStart` and `UserPromptSubmit`) that run the
telemetry helper, which asks the deployment at the start of each session, with the same signed identity as
telemetry, so a change applies to the next session with nothing to install again. If it can't ask (signed out,
offline), it lets people work. Hooks in `~/.claude/settings.json` are a guardrail a person can remove; managed
settings (MDM) carry the same hooks, and those can't be overridden.

**Budgets.** A default and per-person limits in USD, daily, weekly and monthly, in any mix. Every 10 minutes (and
whenever an admin saves) the enforcer compares each person's spend with their limits; anyone over is denied by
`aws:userid` `*:<user name>`. Spend is the larger of Claude Code's estimate and the bill, so turning telemetry off
doesn't escape a budget for longer than the bill's delay. A person's own limits replace the default's one by one,
and a limit of 0 blocks someone outright. IAM compares session names case-sensitively, so type a person's name as
Identity Center spells it; once they've used Claude Code, the enforcer knows their spelling itself. Periods are UTC,
weeks start on Monday.

The deny lifts by itself when the period ends, when an admin raises the limit, or when an admin presses **Reset**
for that person (or **Reset everyone**): their spend so far in the current periods stops counting, as if the
periods had just ended, and they can work again within seconds. Whoever is blocked is told at the top of the
dashboard, and the Controls tab shows where everyone stands.

It's a guardrail, not a meter: someone can overshoot by what they spend in up to 10 minutes plus the telemetry delay
(up to 5 minutes), and the estimate is at list price. The policy holds about 150 blocked people at once (IAM's 6 KB
limit); beyond that it keeps those furthest over, and the Controls tab names the rest. Telemetry is filed by the day
it reaches S3, so the first few minutes after midnight UTC can count towards the day before.

## Capacity and quotas

Everyone shares the Bedrock account's quotas, per model and inference profile: tokens and requests per minute. Look
at them in *Service Quotas → Amazon Bedrock* before rolling out, and ask for increases for the models in
`config.yaml`; long Claude Code sessions send large prompts, mostly from the cache, and the default quotas suit a
team, not an organisation. Cross-region profiles spread the load over their geography's regions; `global.` has
the most capacity, `eu.` keeps requests in the EU.

A new AWS account can start with a Lambda concurrency quota as low as 10, shared by the three functions here: ask
for the default 1,000 in *Service Quotas → AWS Lambda* before rolling out.

Telemetry is limited by Firehose first: 1 MiB/s by default in many regions, about 4,000 Claude Code sessions running
at once. Ask for more in *Service Quotas → Amazon Data Firehose* beyond that; the endpoint itself takes 500 requests
a second. Firehose's `ThrottledRecords` metric, and the ingest function's `lost` log lines, show when it isn't
enough.

Capacity differs by profile, too. On 2026-10-29 in eu-north-1, `eu.anthropic.claude-opus-5-5` refused every
request above about 60K input tokens with `ServiceUnavailableException` (small ones worked), while
`global.anthropic.claude-opus-5-5` took 253K. Try your long prompts on a profile before making it the default.

**Context window.** Sonnet 5, Opus 4.7, Haiku 5.5 and later get the 1M token window on Bedrock without asking
(tested past 200K tokens with Opus, Sonnet and Haiku 5.5); for Opus and Sonnet 4.6, append `[1m]` to the model ID in
`config.yaml`.

## Billed cost

`make deploy` creates a CUR 2.0 export in the Bedrock account (`infra/cur_export.py`): daily, six columns, caller
identity included, into the stack's bucket under `cur/`. A member account's export holds that account's costs; the
dashboard and the budgets keep the Bedrock lines from the permission set's role. If your organisation only allows
exports from another account (usually the management account), set `billing_account` in the config: the bucket then
accepts that account's export, and `make deploy` prints the command to create it there. That export holds every
account's costs: assign the permission set only to the Bedrock account, or roles of the same name elsewhere count as
people too. Cost allocation tags on principals (Identity Center's attributes for access control can pass cost centre
as a session tag) are activated in the management account; the dashboard doesn't need them.

## Data, privacy, cost

- **Collected**: Claude Code's metrics and events: costs, tokens, models, session and prompt ids, tool names, success
  and duration, lines changed, the verified user name, the tags. **Not**: prompts, answers, file contents, tool
  arguments; Claude Code only sends those with `OTEL_LOG_USER_PROMPTS` / `OTEL_LOG_TOOL_DETAILS`, which nothing here
  sets. Bedrock's invocation logging stays off.
- **Kept**: telemetry `retention_days` (default two years) in S3 as Parquet (Firehose converts it), queried
  with Athena: a query reads only the columns it needs. CUR as AWS writes it.
- **Cost** (an estimate, not measured): close to nothing idle; for 100 active people, roughly $5–15 a month (API
  Gateway and Lambda per telemetry request, Firehose and its Parquet conversion, Athena scans for the page and the
  enforcer). Claude Code sends metrics every 60 s and events every 30 s while it runs. Firehose writes them every 5
  minutes, and the dashboard reuses Athena's answer to the same query for 5 minutes, so the page is at most about
  10 minutes behind and a reload costs no scan; the budget check always reads afresh.

## What's left out, and why

- **A gateway.** No fallback to another provider, no central model routing: Bedrock's cross-region inference
  profiles (`eu.`, `global.`) are the routing.
- **Real-time hard caps.** Budgets act within about 15 minutes: the 10-minute check, plus the telemetry delay.
- **Prompt logging.** Off, deliberately.
- **Windows installer.** Use WSL, or managed settings through MDM.

## Taking it down

`make destroy` leaves what it shouldn't take with it, or can't: the data bucket, the CUR export
(`<name>-bedrock-by-principal`, in us-east-1) and, in Identity Center's account, the `<name>-permission-set` stack,
whose permission set then refers to policies that are gone. Delete those by hand when you're done for good.

## Layout

```
config.example.yaml       everything an organisation sets
infra/stack.py            the whole AWS side (CDK); app.py reads the config
infra/cur_export.py       the CUR 2.0 export (Data Exports is us-east-1 only)
infra/permission_set.py   `make permission-set`: finds the groups by name, deploys idc/permission-set.yaml
infra/make_client.py      `make client`: dist/ for MDM
idc/permission-set.yaml   the Identity Center permission set and its group assignments
functions/ingest.py       OTLP in, identity verified, rows to Firehose; and the required tags
functions/api.py          the dashboard's API: who you are, usage, controls, the installer
functions/enforcer.py     budgets, models and regions to two IAM denies
functions/models.py       model IDs and names, and the /model picker's choices
functions/athena.py       runs the queries, several at once
functions/client_files.py the installer and managed settings, filled in
functions/people.py       who counts as a person: the Identity Center role's sessions
client/install.sh         what people run
client/otel-helper.py     gives Claude Code its credentials (signing in first), signs the telemetry with their
                          identity, adds the CC_* tags, checks the required ones
web/                      the dashboard: one page, no build step
tests/                    unit tests, with real Claude Code telemetry as fixtures
docs/                     this page, identity providers, the README's pictures and how they're made
```
