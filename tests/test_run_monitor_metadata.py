"""Additive metadata migration and bounded safe file-change records."""
import unittest
from sqlalchemy import create_engine, text, inspect
from app.migrations.v002_changed_files import upgrade


class MonitorMetadataTests(unittest.TestCase):
    def test_existing_attempt_rows_survive_version_two(self):
        engine=create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE project_model_attempts (id INTEGER PRIMARY KEY, model TEXT)'))
            connection.execute(text("INSERT INTO project_model_attempts VALUES (1, 'offline-model')"))
            upgrade(connection); upgrade(connection)
            row=connection.execute(text('SELECT model, changed_files FROM project_model_attempts')).one()
            self.assertEqual(tuple(row),('offline-model','[]'))
            self.assertIn('changed_files',[c['name'] for c in inspect(connection).get_columns('project_model_attempts')])
