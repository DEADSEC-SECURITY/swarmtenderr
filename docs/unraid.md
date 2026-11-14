# Install Swarmtenderr on Unraid

The [Docker template](../templates/swarmtenderr.xml) runs the published image as
UID/GID `10001`, with a read-only root filesystem and persistent appdata. It does
not need the Docker socket, media libraries or qBittorrent's download directory.
Configure your existing qBittorrent 5 connection in Swarmtenderr after signing in.

**Template status:** Docker lifecycle acceptance is run in CI. Native Unraid
installation, template editing and update acceptance are still required before
Community Apps submission. This is a beta template, not an approved CA listing.

## Prepare private files

Open the Unraid terminal. Adjust the two host paths if your appdata share lives
elsewhere. The secret directory is separate from the application's data.

```sh
install -d -m 0700 -o 10001 -g 10001 \
  /mnt/user/appdata/swarmtenderr \
  /mnt/user/appdata/swarmtenderr-secrets
read -r -s -p 'Initial admin password (12–256 characters): ' swarmtenderr_password
printf '\n'
(umask 077; printf '%s' "$swarmtenderr_password" > /mnt/user/appdata/swarmtenderr-secrets/admin-password)
unset swarmtenderr_password
chown 10001:10001 /mnt/user/appdata/swarmtenderr-secrets/admin-password
chmod 0600 /mnt/user/appdata/swarmtenderr-secrets/admin-password
```

Use your own password. Do not paste its value into template fields, issue reports,
Terraform, screenshots or a Docker command line. The bootstrap password creates
the first `admin` account on an empty database; changing this file later does not
reset an existing account. See [recovery](operations.md) for account recovery.

## Configure the template

For a manual beta test, download `templates/swarmtenderr.xml` from this repository
to `/boot/config/plugins/dockerMan/templates-user/my-swarmtenderr.xml`. In Unraid's
Docker tab choose **Add Container**, then select the Swarmtenderr template.

1. Keep bridge networking. The default host port is `8788`; container port is `8787`.
2. Set **Public URL** to the exact origin you will open in your browser, for example
   `http://192.0.2.10:8788` with your actual server IP. For a TLS reverse proxy, use
   its HTTPS origin. The app checks this origin on sign-in and settings changes.
3. Confirm the appdata and private-secret paths match the prepared directories.
4. Apply, open the Web UI and sign in as `admin` with your private password.
5. Save and test the qBittorrent connection in **Settings → qBittorrent**.
6. Keep **Observe** mode while checking ranking explanations. Select managed
   categories before enabling automatic enforcement.

The image owns its runtime user; `PUID` and `PGID` environment variables do not
change it. If startup reports a permission problem, verify UID/GID `10001` can
write appdata and read the private password file. Do not solve it with privileged
mode or world-writable secrets.

## Use an existing PostgreSQL database

SQLite is the default. To use PostgreSQL, provision a dedicated database/schema
and owning role, then create a mode-`0600`, UID/GID-`10001` file named `postgres-url`
inside the private secret directory. It contains the connection URL; remote
connections should use `sslmode=verify-full` with the appropriate CA trust.
Set the optional connection-file field to
`/run/secrets/swarmtenderr/postgres-url`. Follow the [database transfer and trust
instructions](operations.md) before moving an existing SQLite installation.
Changing the connection field alone does not transfer your old data.

## Update and back up

The template pins a version tag. Read the release notes, take a portable backup
with `swarmtenderr-maintenance`, and retain the previous image/database/key set
before changing **Repository** to the next accepted version. Recreating the
container must retain the same appdata mapping. Do not automatically downgrade
after a schema migration. Keep PostgreSQL backups under its existing database
owner. See [operations](operations.md) for complete backup and restore commands.

## Native Unraid acceptance checklist

Use an isolated beta installation and report the Unraid version and image digest:

- Install through Add Container; verify all labels, required Public URL and Web UI link.
- Confirm sign-in, saving/testing the client connection and live updates.
- Restart and recreate the container; verify accounts, settings and history persist.
- Exercise Unraid's edit/update workflow and take/restore a portable backup.
- Test a remote PostgreSQL connection if you use one.
- Confirm the container remains unprivileged and cannot write its mounted secrets.
- Remove only the beta container. Confirm production containers were unaffected.

File the results in [GitHub Issues](https://github.com/DEADSEC-SECURITY/swarmtenderr/issues).
Redact credentials, session cookies, private hostnames and torrent data.
