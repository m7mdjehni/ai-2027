"""Version 1: additive durable repair records. Never drops the projects table."""
def upgrade(connection):
    from app.run_models import ProjectRun, RunEvent, ModelAttempt, RunValidation
    for model in (ProjectRun, RunEvent, ModelAttempt, RunValidation):
        model.__table__.create(connection, checkfirst=True)
