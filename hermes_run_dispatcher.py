"""Single bounded host dispatcher; PostgreSQL owns admission and leases."""
import os
import threading
import uuid
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from app.run_models import ProjectRun
from app import run_store as store
from hermes_repair import repair_project
from hermes_checkpoint_retention import retain


class Dispatcher:
    def __init__(self, sessions, runner=repair_project):
        self.sessions=sessions; self.runner=runner
        self.worker=uuid.uuid4().hex; self.stopping=threading.Event()
        self.current_cancel=None

    def transaction(self, callback):
        with self.sessions.begin() as db:
            return callback(db)

    def once(self):
        self.transaction(store.recover)
        run_id=self.transaction(lambda db:store.claim(db,self.worker))
        if not run_id: return False
        with self.sessions() as db:
            run=db.get(ProjectRun,run_id)
            project,revision=run.project_id,run.requested_revision
        cancel=threading.Event(); done=threading.Event()
        self.current_cancel=cancel
        def heartbeat():
            while not done.wait(2):
                try:
                    if self.stopping.is_set() or self.transaction(lambda db:store.renew(db,run_id,self.worker)):
                        cancel.set()
                except Exception:
                    cancel.set()  # DB loss/expired ownership must stop, never silently continue.
                    return
        guard=threading.Thread(target=heartbeat,daemon=True); guard.start()
        def observe(kind,data):
            try:
                self.transaction(lambda db:store.observe(db,run_id,self.worker,kind,data))
            except Exception:
                cancel.set()
                raise
        try:
            if self.transaction(lambda db:store.renew(db,run_id,self.worker)): cancel.set()
            result=self.runner(project,revision,run_id,cancel_event=cancel,observer=observe)
            self.transaction(lambda db:store.finish(db,run_id,self.worker,result))
            if result.get('worker_cleanup_confirmed') and all(v.get('cleanup_confirmed') for v in result.get('validations',[])):
                retain(run_id,result)
        except Exception:
            # The lease and active-project reservation remain until expiry/recovery.
            # Never release protection based on an exception or a missing worker.
            cancel.set()
        finally:
            done.set(); guard.join(timeout=3); self.current_cancel=None
        return True

    def serve(self):
        while not self.stopping.is_set():
            try: work=self.once()
            except Exception: work=False
            if not work: self.stopping.wait(1)

    def stop(self):
        self.stopping.set()
        if self.current_cancel: self.current_cancel.set()


def host_sessions():
    # Credentials stay in process configuration, never in events or command arguments.
    url=URL.create('postgresql',username=os.getenv('POSTGRES_USER','assistant'),
        password=os.getenv('POSTGRES_PASSWORD','changeme'),host='127.0.0.1',
        port=int(os.getenv('POSTGRES_PORT','5432')),database=os.getenv('POSTGRES_DB','assistantdb'))
    engine=create_engine(url,echo=False,pool_pre_ping=True,pool_timeout=3,connect_args={'connect_timeout':3,
        'options':'-c statement_timeout=3000 -c lock_timeout=2000'},
                         execution_options={'isolation_level':'READ COMMITTED'})
    return sessionmaker(engine,expire_on_commit=False)
