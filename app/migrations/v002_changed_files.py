"""Version 2: bounded file-change metadata for the existing run monitor."""
from sqlalchemy import inspect, text


def upgrade(connection):
    if 'changed_files' not in {c['name'] for c in inspect(connection).get_columns('project_model_attempts')}:
        connection.execute(text("ALTER TABLE project_model_attempts ADD COLUMN changed_files JSON NOT NULL DEFAULT '[]'"))
