# Swarmtenderr

A self-hosted qBittorrent dashboard and queue manager with dark/light themes, live WebSocket updates, SQLite or PostgreSQL, and Terraform deployment.

See the [installation and app documentation](components/swarmtenderr/README.md), [Terraform module](modules/swarmtenderr/README.md), and [CI/CD workflow](.github/workflows/swarmtenderr.yml).

Docker Hub publishing targets `elgon2003/swarmtenderr`. Verify a successful release before pulling a version. Standalone v0.1 provides native queue monitoring and opt-in manual controls; automatic Apollo scheduling is an external integration.

MIT licensed.
