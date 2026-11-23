"""The migrations, not ``create_all``, are the production schema path."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from app.extensions import db


def _reset_schema():
    db.session.remove()
    with db.engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def test_migrations_match_models_and_are_reversible(app):
    with app.app_context():
        _reset_schema()
        try:
            upgrade(directory="migrations")

            with db.engine.connect() as conn:
                diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
            assert diff == [], f"Models and migrations have drifted: {diff}"

            tables = set(inspect(db.engine).get_table_names())
            assert {"users", "conversations", "messages", "calls", "sent_gifts"} <= tables

            downgrade(directory="migrations", revision="base")
            remaining = set(inspect(db.engine).get_table_names()) - {"alembic_version"}
            assert remaining == set()
        finally:
            _reset_schema()
