# Identity providers

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
