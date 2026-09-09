"""Real PostgreSQL transactions in disposable schemas; Hermes remains mocked."""
import os
from pathlib import Path
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from dotenv import load_dotenv

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_runs
from app import run_store as store


class PostgresDurableTests(test_runs.DurableTests):
    def make_engine(self):
        load_dotenv(Path(__file__).resolve().parents[1]/'.env')
        url=URL.create('postgresql',username=os.getenv('POSTGRES_USER'),password=os.getenv('POSTGRES_PASSWORD'),
            host='127.0.0.1',port=5432,database=os.getenv('POSTGRES_DB'))
        admin=create_engine(url,connect_args={'connect_timeout':3})
        schema='phase3_test_'+uuid.uuid4().hex
        with admin.begin() as db:db.execute(text('CREATE SCHEMA '+schema))
        def cleanup():
            # Identifier is exclusively generated above, never a filesystem/user path.
            with admin.begin() as db:db.execute(text('DROP SCHEMA '+schema+' CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        return create_engine(url,connect_args={'options':'-c search_path='+schema+' -c statement_timeout=5000'})

    def test_concurrent_claims_only_one_owner(self):
        self.submit(); barrier=threading.Barrier(2)
        def claim(worker):
            barrier.wait()
            return self.tx(lambda db:store.claim(db,worker))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(claim,['c'*32,'d'*32]))
        self.assertEqual(results.count(self.run_id),1); self.assertEqual(results.count(None),1)

    def test_concurrent_submission_one_project_reservation(self):
        from sqlalchemy.exc import IntegrityError
        barrier=threading.Barrier(2)
        def submit(run_id):
            barrier.wait()
            try:
                self.tx(lambda db:store.submit(db,self.project,run_id,self.revision))
                return True
            except IntegrityError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(submit,['b'*32,'d'*32]))
        self.assertEqual(sum(values),1)


if __name__=='__main__':unittest.main(verbosity=2)
