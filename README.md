# Claude Code on Amazon Bedrock, for an organisation

An opinionated, small path to giving a whole organisation Claude Code on Amazon Bedrock:
people sign in with the company account they already have, every call is billed to them by name, there's a
self-service page to see your own usage and download the setup, and admins set budgets and allowed models that
Bedrock enforces.

![How it works: Claude Code calls Bedrock as each person; telemetry, the bill, the dashboard and the controls sit beside it](docs/images/architecture.png)

Nothing sits between Claude Code and Bedrock, and nothing is always on: no servers, no database.

## Buy, run a gateway, or this

| | Inference goes through | Per-user cost | Runs |
|---|---|---|---|
| **Buy**: hosted LiteLLM, OpenRouter… | their servers | their feature | nothing |
| **Gateway**: LiteLLM OSS, Anthropic's [Claude apps gateway](https://code.claude.com/docs/en/claude-apps-gateway) | your server | the gateway's (estimated) | a service + Postgres, in the hot path |
| **This**: direct to Bedrock, IAM Identity Center | AWS only | **on the AWS bill, by name** | serverless, idle when unused |

Buying is the simplest by far, if sending inference through a third party is acceptable. A gateway makes sense
when you want one endpoint for many providers, fallbacks, or people to sign in to your IdP with no AWS anywhere;
Anthropic's own gateway now does group-based policies and spend caps, but it calls Bedrock with one AWS identity,
so the bill sees one user. For an organisation that already runs AWS and Identity Center, the path here is less to
run, and the bill itself knows who spent what.

### Why the bill knows

Since April 2026 Bedrock records the calling IAM principal on every inference line in CUR 2.0
([IAM principal cost allocation](https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-iam-principal-tracking.html)).
An Identity Center sign-in is a role session whose name is the person's user name:

```
arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_ClaudeCode_0123456789abcdef/alice@example.com
```

So exact, invoice-level dollars per person come for free, with no gateway and no inference from telemetry. Two
limits: the bill is up to a day behind, and it knows people, not projects. Telemetry fills both gaps: Claude Code's
own cost estimate, current to within minutes, with the tags (project, team…), model, session, tool and
prompt detail the bill doesn't have. The dashboard shows both, side by side, and flags anyone who is billed but
sends no telemetry.

The same identity carries through everything: the telemetry is signed with the person's own Identity Center
credentials (below), budgets deny that session name in IAM, and the dashboard matches people by the same name.

## What you need

- **An AWS account for Bedrock** (the "Bedrock account"), with quotas to match ([Capacity](#capacity-and-quotas)).
  Bedrock subscribes the account to a model the first time someone calls it, and the permission set allows that.
  Give people no other role there: a broader one would get round the controls, and its spend wouldn't show.
- **IAM Identity Center** for the organisation, with a group for who should have Claude Code, `ClaudeCode` say.
  Federated to your IdP and synced from it is usual, but its own directory works too. Who is an admin isn't decided
  here but in the dashboard's IdP.
- **An OIDC client in the same IdP** for the dashboard ([how, per IdP](#identity-providers)).
- **To deploy:** the AWS CLI, [uv](https://docs.astral.sh/uv/) and Node.js. Nothing to install globally: the
  Makefile runs the CDK CLI through `npx`.
- **On laptops:** macOS or Linux with `python3` and `curl` (on a Mac, the Command Line Tools; on Linux, also `unzip`).
  The installer adds Claude Code and the AWS CLI to the home folder if they're missing. Windows: WSL, or MDM with
  managed settings.

## Set up: who does what, where

In a larger organisation this touches three teams, each in a place they already own. None of them is asked for
more than usual, and after the first time, only the platform team is involved again.

| Team | Where | Does | Gives back |
|---|---|---|---|
| Cloud platform | the Bedrock account | `make deploy` | the dashboard's URL and redirect URI |
| Identity | your IdP (Entra ID, Keycloak, Okta…) | one OIDC client for the dashboard | its issuer and client ID |
| Identity Center admins | the management account, or its delegated administrator | the permission set, assigned to groups | the access portal URL |

Permission sets are created and assigned in Identity Center, so that part happens where Identity Center lives. What
the permission set allows doesn't: it refers by name to three IAM policies in the Bedrock account, which the stack
owns. Who is over budget, which models and regions are allowed: all of it changes in the Bedrock account, without
going back to the Identity Center admins.

1. **Bedrock account** (platform team): check the quotas ([Capacity](#capacity-and-quotas)). If Anthropic's models
   have never been used in the account, Bedrock may ask for a short use-case form in its console first.
2. **Deploy** (platform team): `cp config.example.yaml config.yaml` and fill it in. Before the first deploy you
   need Identity Center's access portal URL and home region (its console's *Settings*), and your IdP's issuer;
   the OIDC client usually doesn't exist yet, so leave `client_id` and `audience` as placeholders. If you want a
   name of your own, do step 1 of [Your own domain](#your-own-domain) now and fill `domain`, or the redirect URI
   changes later. Then, with the Bedrock account's credentials, `make deploy`: about ten minutes the first time,
   safe to run again after any change. It bootstraps CDK, deploys the stack, creates the CUR 2.0 export (its first
   data arrives within 24 hours) and prints `Url` and `RedirectUri`.
3. **OIDC client** (identity team): a public client with the authorization code flow and PKCE, the groups mapped
   to `admin` and `user` ([per IdP](#identity-providers)), and `RedirectUri` as its redirect URI. Put its client
   ID (and audience) in `config.yaml` and run `make deploy` again.
4. **Permission set** (Identity Center admins), with their own credentials: `make permission-set`, from a clone
   of this repository with the same `config.yaml` (and `uv`). It deploys a CloudFormation stack,
   `<name>-permission-set`, in their account in `identity_center.region`. The groups must already exist in
   Identity Center. It finds the region's Identity Center instance, finds the groups by name and
   deploys [`idc/permission-set.yaml`](idc/permission-set.yaml). The same by hand in the console: *IAM Identity
   Center → Permission sets → Create*, a custom permission set named as `identity_center.permission_set`, session
   duration as `identity_center.session_duration` (12 hours by default), no AWS managed policies, and these customer
   managed policies: `<name>-bedrock`, `<name>-over-budget`, `<name>-models-off`. Then *AWS accounts → the Bedrock
   account → Assign users or groups*, with the groups and that permission set.
5. **Hand it out**: people sign in to the dashboard and download the installer from its **Set up** tab, or
   `make client` writes the same installer and managed settings to `dist/` for MDM or any other channel. The
   installer has no secrets in it. Then [check it end to end](#checking-a-deployment).

Updating later is `make deploy` again, by the platform team alone.

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

```bash
bash install-claude-code.sh [--no-login]
```

![The dashboard's Set up tab: the installer, how to use it, and the tags the organisation asks for](docs/images/dashboard-setup.png)

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

**Tags.** Every `CC_*` variable in a Claude Code settings file's `env` becomes a tag on your usage, which the
dashboard filters and groups by: `CC_PROJECT=apollo` is `project=apollo`, `CC_TEAM=payments` is `team=payments`.
Tags belong to the folder you work in, so working on two projects at once just works. Put them in the repository,
where Claude Code reads its settings anyway:

```json
// .claude/settings.json (committed, for everyone working in it), or .claude/settings.local.json (just you)
{ "env": { "CC_PROJECT": "apollo", "CC_TEAM": "payments" } }
```

For one session, put them on the command line: `CC_PROJECT=apollo claude`. Settings files beat the shell, so in a
repository that sets its own, override with `claude --settings '{"env": {"CC_PROJECT": "apollo"}}'`, which keeps
the repository's other tags. Without any, usage has no tags. A new session picks a change up at once, a running one
within 5 minutes (when Claude Code next runs the helper). Usage already recorded keeps its tags. Admins can
require some ([Controls](#controls-models-regions-tags-and-budgets)).

**MDM.** `make client` writes `dist/managed-settings.json` for `/Library/Application Support/ClaudeCode/` (macOS) or
`/etc/claude-code/` (Linux) and `dist/claude-otel-helper` for `/usr/local/bin/`. Managed settings can't be
overridden, so telemetry (and with it budgets' estimate) and the tag check stay on, and they allow only Bedrock as
the provider. The installer leaves its own tag-check hooks out where managed settings carry them. They expect the
AWS CLI where its own installer puts it, `/usr/local/bin/aws`. People still need the profile in `~/.aws/config`: run
`dist/install-claude-code.sh --no-login` as them, or write those lines yourself.

**What a repository can change.** Claude Code passes a repository's `.claude/settings.json` `env` to the commands it
runs, `PATH`, `HOME` and `PYTHONPATH` included. The helper doesn't let that pick its programs: it runs the Python
the installer found, in isolated mode, and the AWS CLI by its full path, with a clean environment and this account's
real home. `dist/claude-otel-helper` names `/usr/bin/python3`, which on macOS is Apple's stub and follows
`DEVELOPER_DIR`; if you rely on `allowManagedHooksOnly` to keep repositories from running code, change its first
line to the interpreter itself. It starts again without a repository's OpenSSL settings (`OPENSSL_*`, `SSL_CERT_*`),
gives the AWS CLI no CA bundle from it (behind a TLS-inspecting proxy, put `ca_bundle` in the profile in
`~/.aws/config`, which the installer keeps), and gives no identity to telemetry set to go anywhere but the
dashboard. What the dynamic loader reads before any program starts (`DYLD_*`, `LD_PRELOAD`), and a repository's own
proxy with a CA certificate of its own (`HTTPS_PROXY` with `NODE_EXTRA_CA_CERTS`, which Claude Code itself reads),
it can't keep out: managed settings can set those for everyone. `awsCredentialExport` is the helper;
`awsAuthRefresh` runs as Claude Code decides, and a repository you trust can run hooks of its own anyway.

### How telemetry proves who sent it

Claude Code calls `otelHeadersHelper` at start and every 5 minutes (the installer sets that). The helper presigns an
STS `GetCallerIdentity` request with the person's Identity Center credentials and sends the URL as a header. The
ingest function calls it: STS answers with the caller's role and session name, which must be this account's Claude
Code role. That's the pattern EKS uses for IAM authentication. It means there is no shared ingest token to
distribute or rotate, nobody outside the permission set can send telemetry, and nobody can send it in someone else's
name.

## The dashboard

Signed in with your IdP. Admins see everyone and can filter to anyone; everyone else sees only themselves (the API
enforces it, whatever the page asks for).

- **Usage**: estimated and billed spend, people, sessions, tokens and cache share, lines changed, commits, pull
  requests, active time, spend per day by model, and tables by person (estimated and billed side by side), tag,
  model, source and tool. Click a person or a tag to drill into them; the address keeps the range and filters, so
  a link shows someone the same.
- **Controls** (admins): which models and regions are allowed, the default model, the tags sessions must carry,
  budgets per person, where everyone stands, and resets ([below](#controls-models-regions-tags-and-budgets)).
- **Set up**: the installer, how to use it, and the tags the organisation asks for.

![The Usage tab: spend, people, tags, models and tools for the last 30 days](docs/images/dashboard-usage.png)

## Controls: models, regions, tags and budgets

Admins govern Claude Code from the dashboard's **Controls** tab, and Bedrock enforces it, through two IAM policies
in the Bedrock account that the permission set includes: `<name>-models-off` and `<name>-over-budget`. Whatever
they deny, Bedrock refuses at once, from any client, and its refusal names the policy, so people see why:

```
API Error: 403 … is not authorized to perform: bedrock:InvokeModel … with an explicit deny in an identity-based
policy: arn:aws:iam::111122223333:policy/claude-code-models-off
```

![The Controls tab: models, where requests may go, required tags, budgets, and who's blocked](docs/images/dashboard-controls.png)

Saving applies within a minute.

**Models.** The tab lists the Anthropic models Bedrock offers in the region. One turned off is denied for everyone,
on its inference profiles and the model itself. The allowed ones are what Claude Code's `/model` picker offers, a
row for each routing that's on (`Opus 5.5 · EU`, `Opus 5.5 · Global`; a model with one routing keeps its plain
name), and the one marked **Default** is what new sessions start on; what someone picks in `/model` stays their
choice until they run the installer again. The installer writes these as Claude Code's `modelPicker` and
`ANTHROPIC_DEFAULT_MODEL`, so people get a new list by downloading and running it again; Bedrock enforces a change
straight away either way. Any model can be off, `config.yaml`'s too. Claude Code also makes small background calls
(a session's title, say) with `config.yaml`'s haiku model; with that one off, they fail quietly and sessions carry
on.

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

## Identity providers

Everything about the IdP is in `config.yaml`'s `oidc`; nothing in the code is specific to one. The page signs in
with the authorization code flow and PKCE (a public client, no secret), and sends the token you choose to the API,
where API Gateway checks its signature, issuer, audience and expiry. The API then reads `user_claim` (who you are;
it must equal the Identity Center user name) and `roles_claim` (a dot path to a list), and compares it with
`admin` and `user`. Grant `user` to the group you assign the permission set to, and one membership gives both
Claude Code and the dashboard; grant `admin` to the few who run it.

The two are checked apart, on purpose, and there is nothing to add to either: the IdP decides who opens the
dashboard (and who is an admin of it), and Identity Center decides who gets Claude Code. Someone the IdP lets in
but Identity Center doesn't can see the dashboard and download the installer, but its sign-in gives them no role
in the Bedrock account, so Claude Code can't call Bedrock and the ingest function refuses their telemetry.

`user_claim` decides whose usage someone sees, so it must be a claim people can't change themselves. With `email`,
the API also requires `email_verified` to be true, since many IdPs let people edit their own address; a claim only
admins set (Entra's `preferred_username`, the UPN) is better still.

**Keycloak** (client roles, granted per group):

```yaml
oidc:
  issuer: https://auth.example.com/realms/example
  client_id: claude-code
  token: access
  audience: claude-code                       # needs an Audience mapper on the client
  scopes: openid email
  user_claim: email
  roles_claim: resource_access.claude-code.roles
  admin: [admin]
  user: [user]
```

A public OpenID Connect client with standard flow and PKCE (S256), redirect URI `<Url>/`, web origin `<Url>`,
an Audience mapper adding `claude-code` to the access token, and client roles `admin` and `user` granted to the
groups. Or a Group Membership mapper with `roles_claim: groups`; with "Full group path" on, the values are `/admins`.

The Cognito values are tested as they are. Keycloak is tested with `token: id` instead, on a realm whose ID token
carries the client roles (stock Keycloak puts them in the access token only, hence `token: access` above). The Entra
ID and Okta values are from their documentation.

**Microsoft Entra ID** (app roles, assigned to the groups you sync to Identity Center):

```yaml
oidc:
  issuer: https://login.microsoftonline.com/<tenant id>/v2.0
  client_id: <application (client) id>
  token: id                                   # aud is the client id; roles are in the ID token
  audience: <application (client) id>
  scopes: openid profile email
  user_claim: preferred_username              # the UPN, which is what SCIM gives Identity Center
  roles_claim: roles
  admin: [admin]
  user: [user]
```

An app registration with a Single-page application redirect URI `<Url>/`, app roles `admin` and `user` (member
type Users/Groups), and in Enterprise applications the groups assigned to those roles ("Assignment required" on).
App roles avoid the `groups` claim's GUIDs and its 200-group overage.

**Okta**: a custom authorization server (`issuer: https://<org>.okta.com/oauth2/default`), a `groups` claim in the
ID token with a filter such as `^claude-code-`, `token: id`, `roles_claim: groups`.

**Cognito**: `issuer: https://cognito-idp.<region>.amazonaws.com/<pool id>`, `token: id`, `roles_claim:
cognito:groups`, a public app client with the code flow, the dashboard's URL as both its callback and its sign-out
URL, and a managed login style for it (the hosted sign-in page shows an error without one). Keep self sign-up off:
groups are the access control.

**Google Workspace** doesn't put groups in its tokens, so it needs a broker (Cognito, Dex) in front.

Why not sign in to the dashboard with Identity Center itself? Its OIDC service only offers the device flow the CLI
uses, not one a web page can use.

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

## Checking a deployment

On a laptop, in order:

1. `bash install-claude-code.sh` opens the browser to Identity Center (and on to your IdP), and
   `aws sts get-caller-identity --profile <name>` then shows `assumed-role/AWSReservedSSO_<permission set>_…/<you>`.
   The sign-in reuses whoever the browser is already signed in to the access portal as; with more than one
   Identity Center user, `aws sso login --profile <name> --no-browser` and a private window choose another.
2. `claude` answers, and within about ten minutes you are on the dashboard's Usage page under the same name you
   signed in to the dashboard with. If not: `make logs` shows why the ingest function refused it (an expired
   sign-in, or a role or account that isn't this deployment's), and `/status` in Claude Code shows whether the
   helper works.
3. `aws sso logout`, then `claude`: Claude Code opens the sign-in itself, waits for it, and carries on, with no
   telemetry warning.
4. A daily budget of 0 for yourself on the Controls tab, and within a minute a refusal naming `<name>-over-budget`;
   **Reset** you to recover. A model turned off: `/model` no longer offers it after reinstalling, and asking for
   it by ID is refused naming `<name>-models-off`.
5. A required tag on the Controls tab, then `claude` in a folder without it: the start of the session says what's
   missing, and with **Require** a prompt is held back until `CC_<TAG>` is set.
6. The next day: the Billed column fills in from CUR.

## Status

Tested end to end with Claude Code 2.1.316 against real deployments in an AWS Organization with IAM Identity Center,
with Keycloak and Cognito as the dashboard's identity provider: `make deploy`, with and without a domain of its own;
`make permission-set`; the installer on a clean Mac, installing Claude Code and the AWS CLI itself, and on one with
Bedrock settings of its own; the Identity Center sign-in, and Claude Code opening it when started signed out;
verified telemetry and tags; required tags warning and holding prompts back; the dashboard for admins and for
people; budgets denying, resetting and releasing someone; models and routings turned off and refused; the `/model`
picker and default; and the bill by person from CUR.

## Operating it

```bash
make status          # last telemetry and CUR files, last controls check, who's blocked
make logs            # the ingest function; FN=ApiFunction or FN=Enforcer for the others
make test            # lint and unit tests, no AWS needed
make destroy         # deletes the stack; the data bucket is kept
```

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
docs/                     the README's pictures, and how they're made (AGENTS.md says when)
```

## Licence

MIT: see [LICENSE](LICENSE).
