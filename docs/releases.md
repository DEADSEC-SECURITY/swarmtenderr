# Candidate verification and stable promotion

The public Swarmtenderr repository owns source, versions, the Terraform module,
CI and releases. Apollo consumes a tested immutable image. Its shared policy is
a pinned public source artifact with a commit and SHA-256 manifest; compatibility
imports preserve the existing installed API. Do not maintain a second policy fork.

Pull requests and main run native amd64/arm64 jobs. Each validates JavaScript and
OpenAPI, builds one OCI archive with source labels, SBOM and provenance, loads that
archive and tests the installed runtime on SQLite and PostgreSQL. Real HTTP/socket
and restart checks follow the identity, OIDC failure, history, settings, migration,
policy and recovery suites. Two digest-pinned official qBittorrent clients on a
network without Internet access transfer generated fixture bytes and verify native
controls, protection checks, enforce/observe restoration and completion accounting. Terraform formatting, validation and mocked plans are
required. Fixable high/critical image vulnerabilities fail the job. Accepted OCI
archives are retained as workflow artifacts for 14 days.

Run **Swarmtenderr verify and candidate** with `publish_candidate=true` to publish.
The repository secret `DOCKERHUB_TOKEN` must hold an authorized publishing token for
`elgon2003/swarmtenderr`; never put it in source, chat, issues, build arguments or
workflow output. Publishing copies accepted OCI bytes/attestations without a
rebuild, assembles both architectures, verifies anonymous pull and records the digest.

Use that digest for local and isolated server acceptance. Record source SHA,
image digest, both database results, migration/restore, UI evidence, real client
and Authentik flows, ownership safeguards and rollback. Simulated services do not
prove a live server works.

After acceptance, run **Promote accepted Swarmtenderr digest** with the exact digest
and package version. It promotes the existing manifest, checks the stable tag has
the same digest and performs no Docker build. The stable version and `latest` both
reference the accepted manifest. An existing version cannot be overwritten by a
different digest; upgrade by releasing a new version. Update Apollo's image input through
its existing deployment owner; review a scoped plan and reject unrelated changes.

Back up before migration, retain the previous image/database/key recovery set,
and verify health, TLS, sign-in, completion totals and protected media services
after rollout. Never downgrade the database automatically. Delete feature branches
only after merge and verification.
