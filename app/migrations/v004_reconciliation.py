"""Additive reconciliation metadata; historical publication status is preserved."""
from sqlalchemy import inspect, text


def upgrade(connection):
    existing = {column['name'] for column in inspect(connection).get_columns('project_publications')}
    columns = {
        'reconciliation_status': 'VARCHAR(30)',
        'reconciliation_started_at': 'TIMESTAMP WITH TIME ZONE',
        'reconciliation_finished_at': 'TIMESTAMP WITH TIME ZONE',
    }
    for name, type_name in columns.items():
        if name not in existing:
            connection.execute(text('ALTER TABLE project_publications ADD COLUMN ' + name + ' ' + type_name))