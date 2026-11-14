# Contributing to Swarmtenderr

Installation reports, reproducible bugs, documentation fixes and focused code
changes are welcome. Start with an issue describing the problem or proposed
behavior. One installation manages one qBittorrent client; keep that scope clear
when proposing integrations.

## Report an installation

Use [installation feedback](https://github.com/DEADSEC-SECURITY/swarmtenderr/issues/new?template=installation-feedback.yml).
Include the image version/digest, operating system, processor architecture,
installation method, database and qBittorrent version. Distinguish a synthetic
demo test from a real client test. Include steps, expected behavior and actual
behavior for a bug. Unraid beta reports should cover the checklist in
[the installation guide](docs/unraid.md).

Remove credentials, cookies, connection URLs, private hostnames and torrent data
before sharing logs or screenshots. Never attach a database, encryption key or
portable recovery backup to a public issue.

## Change the code

Keep changes focused and explain the user-visible result and relevant checks in
the pull request. Do not commit secrets or generated private test state. Preserve
the API, data migrations, manual-action audit trail and automatic scheduling
protections unless the change explicitly revises their contract.

Build and run the checks documented in [the README](README.md#develop-and-test)
and [release engineering](docs/releases.md). Container acceptance exercises both
SQLite and PostgreSQL. Real-client fixtures use generated data in isolated
networks; do not use a production torrent client to exercise mutating tests.

The Unraid Docker-contract check parses the actual template and verifies private
file bootstrap, same-origin settings saves, backup and container recreation on
both databases. It does not replace testing Unraid's WebGUI and update workflow.

## AI assistance

AI-assisted contributions are welcome when the contributor reviews the change,
can explain its behavior and reports the checks actually run. Disclose material
AI assistance in the pull request. Do not submit generated test claims, invented
benchmarks or unreviewed bulk changes. Maintainers remain responsible for review
and release acceptance.
