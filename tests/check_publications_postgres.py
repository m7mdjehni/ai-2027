"""Publication migration and admission tests in disposable PostgreSQL schemas."""
import os
import sys
import uuid
import unittest
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_publications
import test_standalone_validation


class PostgresPublications(test_publications.Publications):
    def make_engine(self):
        load_dotenv(Path(__file__).resolve().parents[1]/'.env')
        url = URL.create('postgresql', username=os.getenv('POSTGRES_USER'), password=os.getenv('POSTGRES_PASSWORD'),
            host='127.0.0.1', port=5432, database=os.getenv('POSTGRES_DB'))
        admin = create_engine(url, connect_args={'connect_timeout':3})
        schema = 'phase6_test_' + uuid.uuid4().hex
        with admin.begin() as db: db.execute(text('CREATE SCHEMA ' + schema))
        def cleanup():
            with admin.begin() as db: db.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        return create_engine(url, connect_args={'options':'-c search_path='+schema+' -c statement_timeout=5000'})

    def test_concurrent_confirmations_only_one_repository_owner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from sqlalchemy.exc import IntegrityError
        first, second = self.preview(), self.preview(); barrier = Barrier(2)
        def confirm(row):
            barrier.wait()
            try: self.confirm(row); return True
            except IntegrityError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(confirm, [first,second])),1)

    def test_snapshot_admission_serializes_repair_submission(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from threading import Event
        from app import publication_service, run_store
        started = Event()
        def submit():
            started.set()
            with self.sessions.begin() as db:
                return run_store.submit(db, self.project, 'd'*32, self.revision).id
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.sessions.begin() as db:
                publication_service.gate(db, self.project, self.revision, lock_admission=True)
                future = pool.submit(submit); self.assertTrue(started.wait(2))
                with self.assertRaises(TimeoutError): future.result(timeout=0.1)
            self.assertEqual(future.result(timeout=3), 'd'*32)


class PostgresStandaloneValidation(test_standalone_validation.StandaloneValidationTests):
    make_engine = PostgresPublications.make_engine

    def test_concurrent_validations_append_separate_atomic_records(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from sqlalchemy import select, func
        from app import run_store
        from app.run_models import ProjectRun, RunValidation, RunEvent
        barrier=Barrier(2)
        def record(_):
            with self.sessions.begin() as db:
                barrier.wait(timeout=5)
                return run_store.record_standalone_validation(db,self.project,self.revision,
                    self.result,run_store.now(db)).id
        with ThreadPoolExecutor(max_workers=2) as pool: ids=list(pool.map(record,range(2)))
        self.assertEqual(len(set(ids)),2)
        with self.sessions() as db:
            for model, count in ((ProjectRun,2),(RunValidation,2),(RunEvent,4)):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)),count)
        self.assertEqual(self.preview().status,'preview')


if __name__ == '__main__': unittest.main(verbosity=2)
