"""Carga de las 4 fuentes hacia el esquema común.

- Claves.xlsx / Alcances.xlsx -> versionados (nunca se pisan, solo se agrega
  una versión nueva vigente a partir de una fecha).
- Dataverse.parquet -> backfill histórico único (fuente='dataverse').
- CheLink_YYYYMMDD.txt -> carga diaria en curso (fuente='chelink').
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .schema import (
    alcances,
    carga_log,
    catalogo_claves,
    create_all,
    ventas_inventario_diario,
)
from .upsert import upsert_dataframe


def _log_carga(engine: Engine, tipo: str, nombre: str, filas: int, notas: str = "") -> None:
    with engine.begin() as conn:
        conn.execute(
            carga_log.insert().values(
                tipo_archivo=tipo,
                nombre_archivo=nombre,
                filas_procesadas=filas,
                fecha_proceso=dt.datetime.utcnow(),
                notas=notas,
            )
        )


def load_claves(engine: Engine, file_path: str | Path, vigente_desde: dt.date | None = None) -> int:
    create_all(engine)
    vigente_desde = vigente_desde or dt.date.today()

    df = pd.read_excel(file_path)
    df = df.rename(
        columns={
            "SKU": "sku",
            "UPC": "upc",
            "DESCRIPCON": "descripcion",
            "NOMBRE": "categoria",
            "NOMBRE.1": "subcategoria",
            "RESURTIBLE": "resurtible",
            "EMPAQ": "empaque",
            "E3": "e3",
            "PRECIO UNITARIO": "precio_unitario",
            "COSTO UNITARIO": "costo_unitario",
        }
    )
    cols = [c.name for c in catalogo_claves.columns if c.name != "vigente_desde"]
    df = df[cols].copy()
    df["upc"] = df["upc"].astype(str).str.strip()
    df["vigente_desde"] = vigente_desde

    n = upsert_dataframe(engine, "catalogo_claves", df, ["vigente_desde", "sku"])
    _log_carga(engine, "claves", str(file_path), n, f"vigente_desde={vigente_desde}")
    return n


def load_alcances(engine: Engine, file_path: str | Path, vigente_desde: dt.date | None = None) -> int:
    create_all(engine)
    vigente_desde = vigente_desde or dt.date.today()

    df = pd.read_excel(file_path)
    df = df.rename(
        columns={
            "# de Tienda": "num_tienda",
            "Formato": "formato",
            "Sucursal": "sucursal",
            "Nombre de tienda": "nombre_tienda",
            "Estado": "estado",
            "Ciudad": "ciudad",
            "Nielsen": "nielsen",
            "Area": "area",
            "Clave producto": "sku",
            "Alcance": "alcance",
        }
    )
    cols = [c.name for c in alcances.columns if c.name != "vigente_desde"]
    df = df[cols].copy()
    df["vigente_desde"] = vigente_desde

    n = upsert_dataframe(engine, "alcances", df, ["vigente_desde", "num_tienda", "sku"])
    _log_carga(engine, "alcances", str(file_path), n, f"vigente_desde={vigente_desde}")
    return n


def load_dataverse(engine: Engine, file_path: str | Path) -> int:
    """Backfill histórico único. Solo inserta fechas que no existan ya en la
    tabla (para no pisar datos de CheLink si algún día llegaran a
    traslaparse)."""
    create_all(engine)

    df = pd.read_parquet(file_path)
    df = df.rename(
        columns={
            "Fecha": "fecha",
            "NumTienda": "num_tienda",
            "NumArticulo": "sku",
            "UPC": "upc",
            "Ventas": "ventas_piezas",
            "Inventario": "inventario_piezas",
        }
    )
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"]).copy()
    df["fecha"] = df["fecha"].dt.date
    df["upc"] = df["upc"].astype(str).str.strip()
    df["ventas_pesos"] = pd.NA
    df["inventario_pesos"] = pd.NA
    df["fuente"] = "dataverse"
    df["cargado_en"] = dt.datetime.utcnow()

    cols = [c.name for c in ventas_inventario_diario.columns]
    df = df[cols].copy()

    # No pisar días que ya llegaron por el feed operativo (chelink); el
    # backfill de Dataverse solo debe llenar historia que chelink no cubre.
    with engine.connect() as conn:
        existentes_chelink = pd.read_sql(
            text(
                "SELECT fecha, num_tienda, sku FROM ventas_inventario_diario "
                "WHERE fuente = 'chelink' AND fecha BETWEEN :fmin AND :fmax"
            ),
            conn,
            params={"fmin": df["fecha"].min(), "fmax": df["fecha"].max()},
        )
    if not existentes_chelink.empty:
        existentes_chelink["fecha"] = pd.to_datetime(existentes_chelink["fecha"]).dt.date
        df = df.merge(
            existentes_chelink,
            on=["fecha", "num_tienda", "sku"],
            how="left",
            indicator=True,
        )
        df = df[df["_merge"] == "left_only"].drop(columns=["_merge"])

    n = upsert_dataframe(engine, "ventas_inventario_diario", df, ["fecha", "num_tienda", "sku"])
    _log_carga(engine, "dataverse", str(file_path), n)
    return n


def load_chelink(engine: Engine, file_path: str | Path) -> int:
    """Carga diaria (fuente='chelink'). Reemplaza cualquier fila previa con
    la misma (fecha, tienda, sku), permitiendo recargar un día sin duplicar."""
    create_all(engine)

    df = pd.read_csv(file_path, sep="|", encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()

    df = df.rename(
        columns={
            "dia": "fecha",
            "tienda": "num_tienda",
            "sku": "sku",
            "upc": "upc",
            "VtaNetaPiezas": "ventas_piezas",
            "VtaNetaPesos": "ventas_pesos",
            "InvFinalPiezas": "inventario_piezas",
            "InvFinalVenta": "inventario_pesos",
        }
    )
    df["fecha"] = pd.to_datetime(df["fecha"], format="%Y%m%d").dt.date
    df["num_tienda"] = pd.to_numeric(df["num_tienda"], errors="coerce").astype("Int64")
    df["sku"] = pd.to_numeric(df["sku"], errors="coerce").astype("Int64")
    for c in ["ventas_piezas", "ventas_pesos", "inventario_piezas", "inventario_pesos"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["fuente"] = "chelink"
    df["cargado_en"] = dt.datetime.utcnow()

    cols = [c.name for c in ventas_inventario_diario.columns]
    df = df[cols].dropna(subset=["num_tienda", "sku"]).copy()

    n = upsert_dataframe(engine, "ventas_inventario_diario", df, ["fecha", "num_tienda", "sku"])
    _log_carga(engine, "chelink", str(file_path), n)
    return n
