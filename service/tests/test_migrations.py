from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from p2s_service.db.models import Base


def test_migrations_match_models(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
