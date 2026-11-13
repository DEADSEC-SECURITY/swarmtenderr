"""Offline, portable backups and SQLite/PostgreSQL transfer.

Stop all app instances before restore. Backups contain the runtime encryption
key and identity database: keep them private, encrypted and off-host.
"""
import argparse
import json
import os
from pathlib import Path
import stat

from .secrets import SecretVault
from .settings import Settings
from .store import Store

TABLES = ('schema', 'meta', 'configuration', 'users', 'audit', 'completions',
          'samples', 'snapshot', 'actions', 'policy', 'download_sessions')


def backup(settings, target):
    path = Path(target)
    key_path = Path(settings.secret_key_file or str(Path(settings.data_dir) / 'secret.key'))
    if not key_path.is_file():
        raise ValueError('Runtime key is missing; refusing to create a replacement during backup')
    vault = SecretVault(key_path)
    store = Store(settings.database)
    with store.connection() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY' if store.postgres else 'BEGIN')
        data = {name: store.objects(store.execute(connection, 'SELECT * FROM swarmtenderr_' + name)) for name in TABLES}
    if data['schema'] != [{'version': 2}]:
        raise ValueError('Backup requires the current supported database schema')
    payload = {'format': 'swarmtenderr-backup-v1', 'key': vault.key.decode(), 'tables': data}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(payload, output, allow_nan=False, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        if path.stat().st_size > 256 * 1024**2:
            raise ValueError("Portable backup exceeds the supported size; use a native database backup")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def restore(settings, source):
    path = Path(source)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_size > 256 * 1024**2:
        raise ValueError('Backup must be a private regular file smaller than 256 MiB')
    with path.open() as stream:
        data = json.load(stream)
    if data.get('format') != 'swarmtenderr-backup-v1' or set(data.get('tables', {})) != set(TABLES):
        raise ValueError('Unsupported backup format')
    if data['tables']['schema'] != [{'version': 2}]:
        raise ValueError('Unsupported backup schema')
    from cryptography.fernet import Fernet
    Fernet(data['key'].encode())  # Validate before touching the destination.
    key_path = Path(settings.secret_key_file or str(Path(settings.data_dir) / 'secret.key'))
    if key_path.exists():
        raise ValueError('Restore requires a fresh data directory without an existing runtime key')
    store = Store(settings.database)
    store.initialize()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    try:
        with store.connection(serialize=True) as connection:
            for name in TABLES:
                if name not in ('schema', 'meta') and store.execute(connection, 'SELECT COUNT(*) FROM swarmtenderr_' + name).fetchone()[0]:
                    raise ValueError('Restore requires an empty destination database')
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.write(fd, data['key'].encode())
            os.fsync(fd)
            for name in TABLES:
                store.execute(connection, 'DELETE FROM swarmtenderr_' + name)
                # Column names come from the destination schema, never the backup.
                cursor = store.execute(connection, 'SELECT * FROM swarmtenderr_' + name + ' LIMIT 0')
                columns = [field[0] for field in cursor.description]
                for row in data['tables'][name]:
                    if set(row) != set(columns):
                        raise ValueError('Backup columns do not match the supported schema')
                    store.execute(connection, 'INSERT INTO swarmtenderr_' + name + ' (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', [row[column] for column in columns])
            # Restored sessions and controller leases are deliberately absent.
            store.execute(connection, 'DELETE FROM swarmtenderr_sessions')
            store.execute(connection, 'DELETE FROM swarmtenderr_controller')
            # Never resume a pending mutation or inherit native ceiling ownership
            # on a different host. Recovery always starts in observe mode.
            store.execute(connection, 'DELETE FROM swarmtenderr_policy')
            store.execute(connection, "INSERT INTO swarmtenderr_configuration VALUES ('policy_mode', '\"observe\"') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            store.set_meta(connection, 'config_revision', store.meta('config_revision', 0, connection) + 1)
            store.audit('system.restored', 'offline-maintenance', {'source': 'portable-backup'}, connection)
    except BaseException:
        if fd is not None:
            key_path.unlink(missing_ok=True)
        raise
    finally:
        if fd is not None:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='Offline Swarmtenderr backup, restore and database transfer')
    parser.add_argument('operation', choices=('backup', 'restore'))
    parser.add_argument('--file', required=True, help='Private backup file path; backup refuses to overwrite')
    args = parser.parse_args()
    try:
        globals()[args.operation](Settings.from_environment(), args.file)
    except Exception:
        parser.exit(1, 'Maintenance failed. Check the private file permissions, supported schema and empty destination. No credentials were printed.\n')
    print('Backup created.' if args.operation == 'backup' else 'Restore complete. Start the app in Observe mode and verify totals before enabling enforcement.')


if __name__ == '__main__':
    main()
