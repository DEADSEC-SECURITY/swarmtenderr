# Product acceptance contract

One installation owns one qBittorrent connection. There are no workspace controls.
The following controls have implemented server contracts. Automated evidence and
browser/live acceptance are tracked separately; this document does not certify an
unpublished image or an untested server deployment.

| View/control | Contract | Access | Automated evidence |
| --- | --- | --- | --- |
| Sign in/out, account | `/api/v1/auth/login`, `/auth/logout`, `/me`, `/account/password` | Local account; own account | `test_product.py`, real HTTP/socket acceptance |
| SSO sign in | `/auth/oidc/login`, `/auth/oidc/callback` | Allowed issuer/subject/group | `test_oidc.py`: real signed issuer, PKCE, nonce/state, replay and rejection cases |
| Overview and daily/monthly chart | `/api/v1/status`, `/statistics`, `/live` | Viewer+ | Exact timezone/DST/category totals, large history and pushed observations |
| Torrent search/filter/sort/pages | Current bounded snapshot; local filtered pages | Viewer+ | Backend projection/large queue; browser review required |
| Torrent details/activity | Snapshot, `/api/v1/activity` | Viewer+; administrative audit requires admin | Real action records and protection state |
| Pause/resume/priority/reannounce | `/api/v1/torrents/{hash}/actions` | Operator+, enabled controls | Real official qBittorrent fixtures, fresh eligibility and bounded single-hash mutations |
| Completion filters/pages/CSV | `/api/v1/completions`, `/completions.csv` | Viewer+ | Persistent ledger, category/date filters, formula escaping and 10,000-record export boundary |
| General/client/optimizer/SSO save | `PATCH /api/v1/settings` | Admin | Atomic revision checks, validation, locks, encrypted write-only secrets and restart persistence on both databases |
| Reset section | `/api/v1/settings/reset` | Admin | Explicit deployment defaults, locks and revision conflict tests |
| Client/discovery test | `/api/v1/settings/test-connection`, `/settings/test-sso` | Admin | Incorrect credentials, discovery/signing issuer and real qBittorrent login |
| Add/edit/deactivate users | `/api/v1/users`, `/users/{id}` | Admin | Roles, last-local-admin protection, validation and revocation |
| Admin password reset | `/api/v1/users/{id}/password` | Admin | Password hash, old-session revocation; on-host recovery also tested |
| Live reconnect and access changes | `/api/v1/live` | Authenticated viewer+ | Real sockets, origin rejection, resync, logout, role and deactivation revocation; isolated idle-expiry checks |
| Observe/enforce | Persisted policy, DB-clock exclusive lease | Admin settings | Deterministic policy matrix plus real ceiling/restoration/protection checks |
| Theme | Browser-local preference | Anyone signed in | Browser review at desktop/laptop/phone widths in both themes |

`test_qbit_acceptance.py` starts two digest-pinned official clients on a Docker
network with no Internet access. It generates private fixture torrents, submits
native controls, verifies protected transfers and upload settings, restores the
original ceiling after enforcement, transfers generated data, and checks exactly
one durable completion. Unique test containers/network are removed afterward.

`test_container_acceptance.py` runs the full installed-image unit/integration suite
on SQLite and a disposable PostgreSQL schema, then real HTTP, WebSocket and
persistent-restart checks. Neither suite connects to production qBittorrent.

Required manual/live gates are final keyboard/responsive review, complete UI
workflows, a real Authentik application flow, an anonymous pull of the published
candidate, isolated server acceptance and rollback for that exact digest. Record
those results before stable promotion; a successful unit suite cannot substitute.
