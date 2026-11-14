# OIDC and Authentik

Keep the local administrator available for recovery. Use a verified HTTPS public
URL and trusted identity-provider TLS certificate. Never disable TLS verification.

In Authentik, create a confidential OAuth2/OpenID provider and an application for
Swarmtenderr. Use authorization-code flow, signed JWT ID tokens and S256 PKCE.
Register one strict redirect URI:

```text
https://YOUR_SWARMTENDERR_HOST/auth/oidc/callback
```

Use the provider's displayed **issuer** URL (commonly
`https://identity.example/application/o/swarmtenderr/`) in Settings → Single sign-on.
Swarmtenderr discovers `ISSUER/.well-known/openid-configuration`. Set the client ID,
replace the client secret, choose a login label, and enable SSO. Requested scopes
are `openid profile email`. Include a `groups` array in the verified ID token if
using allowed groups or role mappings; use exact, case-sensitive group names. Do not rely solely on a userinfo-only
group claim. Authentik's scope/property mapping should emit it in the ID token.

Use the discovery check to verify metadata/endpoints/trust, then perform a complete
sign-in to prove the client credentials, redirect and group policy. Discovery alone
does not prove client authentication. A newly provisioned SSO account receives the
configured fallback viewer/operator role.

Set **Administrator groups** (`sso_admin_groups`) and **Operator groups**
(`sso_operator_groups`) in the UI or Terraform deployment defaults to manage roles
from verified group claims. With either list configured, every permitted SSO
account is synchronized on each sign-in: Admin match first, then Operator, then
the fallback role. This updates existing accounts too. The sign-in allowlist
(`sso_allowed_groups`) remains a separate gate; a role match never bypasses it.
Manual role edits are replaced by the group decision at the next sign-in.

Removing a role-group membership demotes the account on its next completed SSO
sign-in and revokes its other app sessions and sockets. Removing allowed-group
membership denies sign-in and revokes existing sessions as well. Swarmtenderr does
not poll the identity provider for membership changes; a change there alone does
not terminate an already established app session before another sign-in or its
normal expiry. Missing or malformed group claims cannot grant a mapped role.

Clearing both role lists returns previously group-managed accounts to the fallback
role on their next sign-in. Installations that never enabled role mappings retain
manual SSO-role management. Email matches never merge identities into a local administrator.
The binding is `(issuer, subject)`, not a display name or email address.

Provider settings, allowed groups and client secrets can also be supplied using
public deployment defaults and `SWARMTENDERR_OIDC_SECRET_FILE`. Secret files are
runtime inputs, not Terraform values. For a private CA, mount a public CA bundle
and set `SSL_CERT_FILE` (the Compose trust overlay and Terraform module support it).
Changing issuer/client/access or role policy revokes SSO sessions, including changes
to deployment defaults detected at startup. Local recovery sessions are preserved. Disabling an account or
changing its role also revokes its established WebSocket stream.

A reverse proxy must preserve the exact public origin and WebSocket upgrades.
OIDC state is short lived, signed, HttpOnly and SameSite=Lax; application session
cookies are Secure under HTTPS. Callback errors expose fixed public codes and do
not log authorization codes, token bodies or credentials. Server HTTP access logging
is disabled to prevent callback query strings reaching ordinary access logs.

Local test coverage includes real HTTP discovery, code exchange with verified PKCE,
RSA JWKS signatures, nonce/state, expiry, issuer/audience failures, replay and group
restrictions. A successful isolated issuer test is separate from live acceptance
against your Authentik application; record that result for the candidate digest.
