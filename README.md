# Claude Code on Amazon Bedrock, for an organisation

An opinionated, small path to giving a whole organisation Claude Code (and Claude Desktop) on Amazon Bedrock:
people sign in with the company account they already have, every call is billed to them by name, there's a
self-service page to see your own usage and download the setup, and admins can set budgets that Bedrock enforces.

```
 laptop                                      the Bedrock account
 Claude Code ── Bedrock calls ─────────────► Amazon Bedrock ─► the bill, by person (CUR 2.0) ──► S3
             ── telemetry, signed by you ──► /v1   ingest λ ─► Firehose ─────────────────────► S3
 browser     ── sign in with your IdP ─────► /     the dashboard
                                             /api  api λ ─► Athena, over both
                                             every 10 min: enforcer λ ─► IAM deny for anyone over budget
```

Nothing sits between Claude Code and Bedrock. Nothing runs when nobody uses it.

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
arn:aws:sts::111122223333:assumed-role/AWSReservedSSO_ClaudeCode_0123abcd/alice@example.com
```

So exact, invoice-level dollars per person come for free, with no gateway and no inference from telemetry. Two
limits: the bill is up to a day behind, and it knows people, not projects. Telemetry fills both gaps: Claude
Code's own cost estimate, current to the minute, with the tags (project, team…), model, session, tool and prompt
detail the bill doesn't have. The dashboard shows both, side by side, and flags anyone who is billed but sends no telemetry.

The same identity carries through everything: the telemetry is signed with the person's own Identity Center
credentials (below), budgets deny that session name in IAM, and the dashboard matches people by the same name.

## What you need

- **An AWS account for Bedrock** (the "Bedrock account"), with access to Anthropic's models (Bedrock asks for a
  short use-case form the first time) and quotas to match.
- **IAM Identity Center** for the organisation, federated to your IdP, with the groups that should have Claude Code
  synced to it, for example `Bedrock` and `Admins`.
- **An OIDC client in the same IdP** for the dashboard ([how, per IdP](#identity-providers)).
- **To deploy:** the AWS CLI, [uv](https://docs.astral.sh/uv/) and Node.js. Nothing to install globally: the
  Makefile runs the CDK CLI through `npx`.
- **On laptops:** macOS or Linux with `python3` (on a Mac, the Command Line Tools). The installer adds Claude Code and
  the AWS CLI to the home folder if they're missing. Windows: WSL, or MDM with managed settings.

## Deploy

Everything you decide goes in one file, and nothing else needs changing.

1. **Configure.** `cp config.example.yaml config.yaml` and fill it in: the Bedrock account, Identity Center, the
   models, your IdP, and optionally your own domain and tags.
2. **Deploy to the Bedrock account**, with its credentials: `make deploy`. About ten minutes the first time; safe to
   run again after any change to the config. It bootstraps CDK, deploys the stack, creates the CUR 2.0 export (its
   first data arrives within 24 hours) and prints the dashboard's `Url` and `RedirectUri`.
3. **Register the redirect URI** in your IdP client. With `domain` in the config you know it beforehand
   (`https://<domain>/`), and can do this first.
4. **Create the permission set**, with the credentials of whoever administers Identity Center (the management
   account, or its delegated administrator): `make permission-set`. It finds the Identity Center instance and the
   config's groups by name, and assigns the permission set to them in the Bedrock account. The permission set
   refers to two IAM policies the stack created, so what the role may do, and who is over budget, is managed in the
   Bedrock account and changes there without touching Identity Center.
5. **Hand it out.** People sign in to the dashboard and download the installer from its **Set up** tab, or
   `make client` writes the same installer and managed settings to `dist/` for MDM or any other channel. The
   installer has no secrets in it.

## On the laptop

```bash
bash install-claude-code.sh [--desktop] [--no-login]
```

It installs Claude Code and the AWS CLI if they're missing, both for the person only, without admin rights:
Claude Code with Anthropic's own installer, which puts it in `~/.local/bin` anyway, and the AWS CLI with the
current-user install AWS documents, into `~/.local/aws-cli`. Then it adds an
`[sso-session]` with your Identity Center portal URL and a `[profile]` with the account and permission set to
`~/.aws/config`, merges the Bedrock and telemetry settings into
`~/.claude/settings.json` (leaving everything else in it alone), installs the telemetry helper, and opens
Identity Center's sign-in in the browser, which goes on to Entra, Keycloak or whatever Identity Center federates
to. After that it's `claude`. When the sign-in runs out, Claude Code runs `aws sso login` itself
(`awsAuthRefresh`), and the CLI renews role credentials silently for as long as the Identity Center session lasts
(set the access portal's session duration to what your security team accepts, up to 90 days).

Claude Code gets its credentials through `awsCredentialExport` rather than `AWS_PROFILE`, so that commands Claude
runs for you (`aws s3 ls`) keep using your own profiles, not the Bedrock account.

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
within 29 minutes (when Claude Code next runs the helper). Usage already recorded keeps its tags.

**Claude Desktop.** `--desktop` (macOS) writes and opens a configuration profile that puts Claude Desktop in
third-party mode on Bedrock, with the same AWS profile (one sign-in for both), the same models and the same
telemetry helper. Approve it in System Settings → General → Device Management. With MDM, push the same keys in the
`com.anthropic.claudefordesktop` domain ([reference](https://claude.com/docs/third-party/claude-desktop/configuration)).

**MDM.** `make client` writes `dist/managed-settings.json` for `/Library/Application Support/ClaudeCode/`
(macOS) or `/etc/claude-code/` (Linux) and `dist/claude-otel-helper` for `/usr/local/bin/`. Managed settings
can't be overridden, so telemetry (and with it budgets' estimate) stays on. They expect the AWS CLI where its own
installer puts it, `/usr/local/bin/aws`. People still need the profile in `~/.aws/config`: run
`dist/install-claude-code.sh --no-login` as them, or write those lines yourself.

### How telemetry proves who sent it

Claude Code calls `otelHeadersHelper` at start and every 29 minutes. The helper presigns an STS
`GetCallerIdentity` request with the person's Identity Center credentials and sends the URL as a header. The ingest
function calls it: STS answers with the caller's role and session name, which must be this account's Claude Code
role. That's the pattern EKS uses for IAM authentication. It means there is no shared ingest token to distribute or
rotate, nobody outside the permission set can send telemetry, and nobody can send it in someone else's name.

## The dashboard

Signed in with your IdP. Admins see everyone and can filter to anyone; everyone else sees only themselves (the API
enforces it, whatever the page asks for).

- **Usage**: estimated and billed spend, people, sessions, tokens and cache share, lines changed, commits, pull
  requests, active time, spend per day by model, and tables by person, tag, model, source (main conversation,
  subagents, Claude Code's own calls) and tool. Click a person or a tag to drill into them.
- **Sessions**: recent sessions; open one to see it prompt by prompt, each model call (model, tokens, cost,
  latency) and tool call (success, duration).
- **Budgets** (admins): a default and per-person daily, weekly and monthly limits in USD, and where everyone
  stands.
- **Set up**: the installer, and how to use it.

## Budgets

Every 10 minutes the enforcer compares each person's spend today, this week and this month with their limits. Spend
is the larger of Claude Code's estimate and the bill, so turning off telemetry doesn't escape a budget for longer
than the bill's delay. People over a limit are written into the `<name>-budget` IAM policy, a deny on
`aws:userid` `*:<user name>`; the permission set includes it, so Bedrock refuses them, from any client, until the
period ends or an admin raises the limit. Periods are UTC, weeks start on Monday. A person's own limits replace
the default's one by one, and a limit of 0 blocks someone outright.

It's a guardrail, not a meter: someone can overshoot by what they spend in up to 10 minutes plus the telemetry
delay, and the estimate is at list price. The policy holds about 150 blocked people at once (IAM's 6 KB limit).

What they see in Claude Code is Bedrock's own refusal, which names the policy:

```
API Error: 403 … is not authorized to perform: bedrock:InvokeModelWithResponseStream … with an explicit deny in an
identity-based policy: arn:aws:iam::111122223333:policy/claude-code-budget
```

and the dashboard's Usage page shows them over budget.

## Identity providers

Everything about the IdP is in `config.yaml`'s `oidc`; nothing in the code is specific to one. The page signs in
with the authorization code flow and PKCE (a public client, no secret), and sends the token you choose to the API,
where API Gateway checks its signature, issuer, audience and expiry. The API then reads `user_claim` (who you are;
it must equal the Identity Center user name) and `roles_claim` (a dot path to a list), and compares it with
`admin` and `user`. Grant those to the same groups you assign the permission set to, and one group membership gives
both Claude Code and the dashboard.

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

**Cognito**: `issuer: https://cognito-idp.<region>.amazonaws.com/<pool id>`, `token: id`,
`roles_claim: cognito:groups`, a public app client with the code flow, and a managed login style for it (the
hosted sign-in page shows an error without one). Keep self sign-up off: groups are the access control.

**Google Workspace** doesn't put groups in its tokens, so it needs a broker (Cognito, Dex) in front.

Why not sign in to the dashboard with Identity Center itself? Its OIDC service only offers the device flow the CLI
uses, not one a web page can use.

## Billed cost

`make deploy` creates a CUR 2.0 export in the Bedrock account (`infra/cur_export.py`): daily, six columns,
caller identity included, into the stack's bucket under `cur/`. A member account's export holds that account's
costs, which here are exactly the Bedrock costs. If your organisation only allows exports from another account
(usually the management account), set `billing_account` in the config: the bucket then accepts that account's
export, and `make deploy` prints the command to create it there.
Cost allocation tags on principals (Identity Center's attributes for access control can pass cost centre as a
session tag) are activated in the management account; the dashboard doesn't need them.

## Data, privacy, cost

- **Collected**: Claude Code's metrics and events: costs, tokens, models, session and prompt ids, tool names, success
  and duration, lines changed, the verified user name, the tags. **Not**: prompts, answers, file contents, tool
  arguments; Claude Code only sends those with `OTEL_LOG_USER_PROMPTS` / `OTEL_LOG_TOOL_DETAILS`, which nothing here
  sets. Bedrock's invocation logging stays off.
- **Kept**: telemetry `retention_days` (default two years) in S3, queried with Athena. CUR as AWS writes it.
- **Cost** (an estimate, not measured): close to nothing idle; for 100 active people, roughly $5–15 a month (API
  Gateway and Lambda per telemetry request, Firehose, Athena scans for the page and the enforcer). Claude Code sends
  metrics every 60 s and events every 30 s while it runs.

## What's left out, and why

- **A gateway.** No fallback to another provider, no central model routing: Bedrock's cross-region inference
  profiles (`eu.`, `global.`) are the routing.
- **Real-time hard caps.** Budgets act within about 10 minutes.
- **Prompt logging.** Off, deliberately.
- **Windows installer.** Use WSL, or managed settings through MDM.

## Status

Tested end to end with Claude Code 2.1.312 against a real deployment, with Cognito as the IdP and a plain IAM role
(assumed with a person's name as the session name) standing in for Identity Center's: the installer from the
dashboard, verified telemetry, the dashboard for admins and for people, budgets denying and releasing someone, and
the bill by person from CUR. Not yet tested: the Identity Center sign-in itself (`aws sso login`, and Claude Code
running it when the sign-in expires), `make permission-set`, Claude Desktop, and the installer's AWS CLI install on
macOS.

## Checking a deployment

On a laptop, in order:

1. `bash install-claude-code.sh` opens the browser to Identity Center (and on to your IdP), and
   `aws sts get-caller-identity --profile <name>` then shows `assumed-role/AWSReservedSSO_<permission set>_…/<you>`.
2. `claude` answers, and within two minutes you are on the dashboard's Usage page under the same name you signed
   in to the dashboard with. If not: `make logs` shows the ingest function's answer (401 means the role or
   account didn't match `telemetry_roles`).
3. `aws sso logout`, then `claude`: Claude Code runs `aws sso login` itself and carries on.
4. A tiny daily budget for yourself on the Budgets tab, then within 10 minutes a refusal; remove it to recover.
5. `--desktop`: the profile installs, and Claude Desktop starts in Bedrock mode with the same sign-in.
6. The next day: the Billed column fills in from CUR.

## Operating it

```bash
make status          # last telemetry and CUR files, last budget check, who's blocked
make logs            # the ingest function; FN=Dashboard or FN=Enforcer for the others
make test            # lint and unit tests, no AWS needed
make destroy         # deletes the stack; the data bucket is kept
```

## Layout

```
config.example.yaml       everything an organisation sets
infra/stack.py            the whole AWS side (CDK); app.py reads the config
infra/cur_export.py       the CUR 2.0 export (Data Exports is us-east-1 only)
infra/permission_set.py   `make permission-set`: finds the instance and groups, deploys idc/permission-set.yaml
infra/make_client.py      `make client`: dist/ for MDM
idc/permission-set.yaml   the Identity Center permission set and its group assignments
functions/ingest.py       OTLP in, identity verified, rows to Firehose
functions/api.py          the dashboard's API: who you are, usage, sessions, budgets, the installer
functions/enforcer.py     budgets to an IAM deny
functions/athena.py       runs the queries, several at once
functions/client_files.py the installer and managed settings, filled in
functions/people.py       who counts as a person: the Identity Center role's sessions
client/install.sh         what people run
client/otel-helper.py     signs the telemetry with their identity, adds the CC_* tags
web/                      the dashboard: one page, no build step
tests/                    unit tests, with real Claude Code telemetry as fixtures
```

## Licence

MIT: see [LICENSE](LICENSE).
