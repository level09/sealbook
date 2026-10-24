"""Alembic integration helpers for stk."""

from __future__ import annotations

import importlib
import pkgutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config as AlembicConfig
from sqlalchemy.engine import make_url

import stk
from stk.extensions import Base
from stk.settings import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = PROJECT_ROOT / "alembic"

SYNC_DRIVER_MAP = {
    "postgresql+asyncpg": "postgresql+psycopg2",
    "sqlite+aiosqlite": "sqlite",
}


def import_model_modules() -> None:
    """Import all `stk.<feature>.models` modules so metadata is complete."""
    for module in pkgutil.iter_modules(stk.__path__, prefix="stk."):
        model_module = f"{module.name}.models"
        try:
            importlib.import_module(model_module)
        except ModuleNotFoundError as exc:
            if exc.name != model_module:
                raise


def get_database_url(*, sync: bool = False) -> str:
    """Return the configured database URL, optionally converted for Alembic."""
    database_url = Config.SQLALCHEMY_DATABASE_URI
    if not sync:
        return database_url

    url = make_url(database_url)
    drivername = SYNC_DRIVER_MAP.get(url.drivername)
    if drivername is not None:
        url = url.set(drivername=drivername)
    # str(URL) masks the password as *** on SQLAlchemy >= 2.0.50
    return url.render_as_string(hide_password=False)


def get_target_metadata():
    """Return metadata after loading all model modules."""
    import_model_modules()
    return Base.metadata


# Tables an extension owns (PostGIS keeps its reference systems here); no model
# describes them, so autogenerate and the drift check must not offer to drop them.
EXTENSION_TABLES = {"spatial_ref_sys"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in EXTENSION_TABLES)


def portable_tables() -> list:
    """Every model table except those that need PostGIS, for create_all on SQLite."""
    return [t for t in get_target_metadata().sorted_tables if not t.info.get("postgis")]


def build_alembic_config() -> AlembicConfig:
    """Build an Alembic config bound to the current stk settings."""
    config = AlembicConfig(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", get_database_url(sync=True))
    return config


def _render(url, *, sync: bool) -> str:
    drivername = SYNC_DRIVER_MAP.get(url.drivername) if sync else None
    if drivername is not None:
        url = url.set(drivername=drivername)
    return url.render_as_string(hide_password=False)


@contextmanager
def throwaway_database_url(*, sync: bool = False):
    """Yield the URL of an empty database of the configured dialect, then drop it.

    Postgres apps need Postgres here: their models and migrations use types
    SQLite cannot render. Postgres needs a role with CREATEDB.
    """
    from sqlalchemy import create_engine, text

    url = make_url(get_database_url())
    if url.get_backend_name() != "postgresql":
        with tempfile.TemporaryDirectory(prefix="stk-db-") as temp_dir:
            yield _render(url.set(database=f"{temp_dir}/throwaway.db"), sync=sync)
        return

    name = f"stk_tmp_{uuid.uuid4().hex[:12]}"
    admin = create_engine(
        _render(url.set(database="postgres"), sync=True), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as connection:
            connection.execute(text(f'create database "{name}"'))
        try:
            yield _render(url.set(database=name), sync=sync)
        finally:
            with admin.connect() as connection:
                connection.execute(text(f'drop database "{name}" with (force)'))
    finally:
        admin.dispose()
