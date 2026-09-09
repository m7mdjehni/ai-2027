"""Additive publication history; no changes to repair tables."""
def upgrade(connection):
    from app.publication_models import Publication
    Publication.__table__.create(connection, checkfirst=True)

