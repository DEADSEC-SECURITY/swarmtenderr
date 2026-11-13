"""Each PostgreSQL test owns a new schema; never touch shared application tables."""
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def database_url(case, sqlite_path):
    base = os.environ.get('SWARMTENDERR_TEST_DATABASE_URL', '')
    if not base:
        return 'sqlite:///' + str(sqlite_path)
    import psycopg
    from psycopg import sql
    schema = 'swarm_test_' + uuid.uuid4().hex
    with psycopg.connect(base, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

    def cleanup():
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
    case.addCleanup(cleanup)
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query))
    query['options'] = '-csearch_path=' + schema
    return urlunsplit(parts._replace(query=urlencode(query)))
