"""Additive Actions migration metadata; historical publication state is preserved."""
from sqlalchemy import inspect, text


def upgrade(connection):
    existing = {column['name'] for column in inspect(connection).get_columns('project_publications')}
    columns = {
        'actions_status': 'VARCHAR(30)',
        'actions_workflow_commit': 'VARCHAR(40)',
        'actions_run_id': 'VARCHAR(30)',
        'actions_deployment_id': 'VARCHAR(40)',
        'actions_started_at': 'TIMESTAMP WITH TIME ZONE',
        'actions_finished_at': 'TIMESTAMP WITH TIME ZONE',
    }
    for name, type_name in columns.items():
        if name not in existing:
            connection.execute(text('ALTER TABLE project_publications ADD COLUMN ' + name + ' ' + type_name))