"""Consultas para el dashboard: dimensiones (para filtros) y alertas
enriquecidas con esas dimensiones."""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


def _latest_version(engine: Engine, table: str) -> dt.date | None:
    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT MAX(vigente_desde) FROM {table}")).fetchone()
    return row[0] if row else None


def dim_tiendas(engine: Engine) -> pd.DataFrame:
    """Una fila por tienda (formato/estado/ciudad/nielsen/area), tomada de la
    versión de Alcances más reciente."""
    v = _latest_version(engine, "alcances")
    if v is None:
        return pd.DataFrame(
            columns=["num_tienda", "formato", "sucursal", "nombre_tienda", "estado", "ciudad", "nielsen", "area"]
        )
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT DISTINCT num_tienda, formato, sucursal, nombre_tienda, estado, ciudad, nielsen, area "
                "FROM alcances WHERE vigente_desde = :v"
            ),
            conn,
            params={"v": v},
        )


def dim_productos(engine: Engine) -> pd.DataFrame:
    v = _latest_version(engine, "catalogo_claves")
    if v is None:
        return pd.DataFrame(columns=["sku", "upc", "descripcion", "categoria", "subcategoria"])
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                "SELECT sku, upc, descripcion, categoria, subcategoria "
                "FROM catalogo_claves WHERE vigente_desde = :v"
            ),
            conn,
            params={"v": v},
        )


def alertas_enriquecidas(engine: Engine, fecha_desde: dt.date | None = None, fecha_hasta: dt.date | None = None) -> pd.DataFrame:
    where = []
    params: dict = {}
    if fecha_desde:
        where.append("fecha >= :fd")
        params["fd"] = fecha_desde
    if fecha_hasta:
        where.append("fecha <= :fh")
        params["fh"] = fecha_hasta
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    with engine.connect() as conn:
        alertas = pd.read_sql(text(f"SELECT * FROM alertas_diarias {clause}"), conn, params=params)
    if alertas.empty:
        return alertas

    alertas["fecha"] = pd.to_datetime(alertas["fecha"])
    tiendas = dim_tiendas(engine)
    productos = dim_productos(engine)

    out = alertas.merge(tiendas, on="num_tienda", how="left").merge(productos, on="sku", how="left")
    for col in ["estado", "ciudad", "nielsen", "area", "nombre_tienda", "categoria", "subcategoria"]:
        if col in out.columns:
            out[col] = out[col].fillna("Sin clasificar")
    return out


def carga_log_reciente(engine: Engine, limite: int = 20) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT * FROM carga_log ORDER BY fecha_proceso DESC LIMIT :n"),
            conn,
            params={"n": limite},
        )
