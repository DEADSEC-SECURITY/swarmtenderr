# Swarmtenderr runtime

The authoritative product, installation and development guide is the
[repository README](../../README.md). This directory builds the same SQLite and
PostgreSQL image; `src/swarmtenderr_policy` is the canonical portable policy.

`requirements.versions` pins runtime and transitive versions;
`tools/lock_dependencies.py` refreshes hashes from those exact PyPI releases.
Changes to the runtime dependency graph must update both files and pass the
installed-image acceptance matrix. Development-only validation dependencies do
not belong in this runtime lock.
