from pathlib import Path
from typing import Generator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

sqlite_url = f"sqlite:///{Path(__file__).parents[2] / 'pipeline.db'}"

engine = create_engine(sqlite_url, echo=True)

# Columns added after the first release. `create_all` only creates missing
# *tables*, so an existing pipeline.db keeps its old shape and every query on a
# new column fails with "no such column" — one ALTER each is the whole migration
# story this app needs (single user, single file, no Alembic).
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "project": [
        ("archived_at", "DATETIME"),
        ("archive_path", "VARCHAR"),
    ],
}


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue  # freshly created by create_all — already correct
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
