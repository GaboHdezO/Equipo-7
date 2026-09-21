"""Upsert portable entre SQLite y Postgres (delete + insert por lote, sin
depender de sintaxis ON CONFLICT específica de cada motor)."""
from __future__ import annotations

import uuid

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


def upsert_dataframe(engine: Engine, table_name: str, df: pd.DataFrame, key_columns: list[str]) -> int:
    """Reemplaza en `table_name` las filas cuyas llaves coincidan con `df`,
    y luego inserta todas las filas de `df`. Devuelve el número de filas
    escritas."""
    if df.empty:
        return 0

    staging_table = f"_staging_{table_name}_{uuid.uuid4().hex[:8]}"
    # method="multi" (INSERT de varias filas por sentencia) es mucho más
    # rápido en Postgres, pero en SQLite topa rápido el límite de parámetros
    # por sentencia (999) y termina siendo más lento que el executemany
    # por defecto -- así que solo se usa en Postgres.
    to_sql_kwargs = {"method": "multi", "chunksize": 5000} if engine.dialect.name == "postgresql" else {}
    with engine.begin() as conn:
        df.to_sql(staging_table, conn, if_exists="replace", index=False, **to_sql_kwargs)

        key_tuple = "(" + ", ".join(key_columns) + ")"
        key_list = ", ".join(key_columns)
        conn.execute(
            text(
                f"DELETE FROM {table_name} WHERE {key_tuple} IN "
                f"(SELECT {key_list} FROM {staging_table})"
            )
        )
        columns = ", ".join(df.columns)
        conn.execute(
            text(f"INSERT INTO {table_name} ({columns}) SELECT {columns} FROM {staging_table}")
        )
        conn.execute(text(f"DROP TABLE {staging_table}"))

    return len(df)
