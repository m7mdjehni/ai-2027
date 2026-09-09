"""Explicit ordered migrations, serialized with a PostgreSQL advisory lock."""
from sqlalchemy import text


def migrate(engine):
    from app.migrations.v001_runs import upgrade
    from app.migrations.v002_changed_files import upgrade as changed_files
    from app.migrations.v003_publications import upgrade as publications
    from app.migrations.v004_reconciliation import upgrade as reconciliation
    from app.migrations.v005_actions_migration import upgrade as actions_migration
    with engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text('SELECT pg_advisory_xact_lock(731903)'))
        connection.execute(text('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)'))
        versions = set(connection.execute(text('SELECT version FROM schema_migrations')).scalars())
        if 1 not in versions:
            upgrade(connection)
            connection.execute(text('INSERT INTO schema_migrations(version) VALUES (1)'))
        if 2 not in versions:
            changed_files(connection)
            connection.execute(text('INSERT INTO schema_migrations(version) VALUES (2)'))
        if 3 not in versions:
            publications(connection)
            connection.execute(text('INSERT INTO schema_migrations(version) VALUES (3)'))
        if 4 not in versions:
            reconciliation(connection)
            connection.execute(text('INSERT INTO schema_migrations(version) VALUES (4)'))
        if 5 not in versions:
            actions_migration(connection)
            connection.execute(text('INSERT INTO schema_migrations(version) VALUES (5)'))
