"""Conexión a base de datos.

Por defecto usa un archivo SQLite local (data/warehouse.db), sin necesidad de
ninguna cuenta externa. En cuanto exista la variable de entorno DATABASE_URL
(cadena de conexión de Postgres/Supabase), se usa esa en su lugar, sin cambiar
una sola línea del resto del pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DEFAULT_SQLITE_PATH = DATA_DIR / "warehouse.db"

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Supabase/Postgres suelen dar la URL con prefijo postgres://, SQLAlchemy
        # requiere postgresql://
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        _engine = create_engine(database_url, pool_pre_ping=True)
    else:
        _engine = create_engine(f"sqlite:///{DEFAULT_SQLITE_PATH}")
    return _engine


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"
