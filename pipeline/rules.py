"""Motor de reglas: OOS, Phantom Inventory y venta perdida acumulada.

Reglas confirmadas con el usuario:
- Un día sin fila para una combinación tienda-sku (hueco del feed CheLink) NO
  cuenta como día de incidencia: no suma a la racha ni a la pérdida. Solo se
  procesan días efectivamente reportados.
- OOS: inventario reportado en 0, sin venta ese día, y la combinación tiene
  historial de venta (promedio móvil > 0).
- Phantom Inventory: inventario > 0, sin venta, y la pérdida acumulada de
  unidades esperadas (promedio diario x días sin venta, sumado día a día
  porque el promedio puede variar) alcanza el umbral configurable (1.5
  unidades por defecto).
- La racha y la pérdida acumulada son un solo acumulador continuo que
  atraviesa transiciones OOS <-> PI; solo se reinicia cuando vuelve a
  registrarse una venta.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .schema import alertas_diarias, create_all
from .upsert import upsert_dataframe

UMBRAL_PI_PIEZAS_DEFAULT = 1.5
VENTANA_PROMEDIO_DEFAULT = 90  # observaciones reportadas, no días de calendario
VENTANA_ALERTAS_DIAS_DEFAULT = 400  # cuántos días recientes se reclasifican en cada corrida
LOOKBACK_EXTRA_DIAS = 120  # historia extra para poder calcular el promedio móvil desde el borde de la ventana


def _get_config(engine: Engine, clave: str, default: float) -> float:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT valor FROM config_parametros WHERE clave = :c"), {"c": clave}
        ).fetchone()
    return float(row[0]) if row else default


def _precio_lookup(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT vigente_desde, sku, precio_unitario FROM catalogo_claves ORDER BY sku, vigente_desde"),
            conn,
        )
    df["vigente_desde"] = pd.to_datetime(df["vigente_desde"]).astype("datetime64[ns]")
    return df


def precio_por_fecha(fechas: pd.Series, skus: pd.Series, precios: pd.DataFrame) -> pd.Series:
    """Precio unitario vigente para cada (fecha, sku), por as-of join.

    Si una fecha es anterior a la primera versión de catálogo que existe para
    ese sku (típicamente pasa al recalcular sobre historia vieja, cargada con
    un catálogo cuya vigencia es "hoy"), se usa el precio de la versión más
    antigua disponible en vez de dejarlo en NaN -- es una estimación para
    convertir a pesos, no afecta la detección de OOS/PI que corre en piezas.
    """
    tmp = pd.DataFrame({"fecha_ts": pd.to_datetime(fechas).astype("datetime64[ns]"), "sku": skus})
    encontrado = pd.merge_asof(
        tmp.sort_values("fecha_ts"),
        precios.sort_values("vigente_desde"),
        left_on="fecha_ts",
        right_on="vigente_desde",
        by="sku",
        direction="backward",
    ).sort_index()["precio_unitario"]

    precio_mas_antiguo = precios.sort_values("vigente_desde").groupby("sku")["precio_unitario"].first()
    return encontrado.fillna(tmp["sku"].map(precio_mas_antiguo))


def _clasificar_todo(df: pd.DataFrame, umbral_pi: float) -> pd.DataFrame:
    """Clasificación vectorizada sobre el DataFrame COMPLETO (todas las
    combinaciones tienda-sku a la vez), usando groupby(...).rolling()/
    .cumsum() -- nunca un `for` en Python por combinación, que con miles de
    tiendas x SKUs es la parte que no escala.

    Mismo truco que antes pero aplicado a nivel de todo el dataframe: una
    venta (venta_piezas > 0) reinicia el acumulador; numerar cada racha de
    "sin venta" con el número de ventas ya ocurridas antes de ella (cumsum de
    la bandera de venta, por combinación) agrupa exactamente los días de una
    misma racha, y un cumsum dentro de cada (tienda, sku, racha) da la racha
    y la pérdida acumulada."""
    key = ["num_tienda", "sku"]
    df = df.sort_values(key + ["fecha"]).reset_index(drop=True)

    ventas_prev = df.groupby(key)["ventas_piezas"].shift(1)
    avg = (
        ventas_prev.groupby([df["num_tienda"], df["sku"]])
        .rolling(VENTANA_PROMEDIO_DEFAULT, min_periods=5)
        .mean()
        .reset_index(level=key, drop=True)
    )
    df["promedio_diario_venta"] = avg

    hay_venta = (df["ventas_piezas"] > 0).astype(int)
    df["_segmento"] = hay_venta.groupby([df["num_tienda"], df["sku"]]).cumsum()

    tiene_historial = df["promedio_diario_venta"].notna() & (df["promedio_diario_venta"] > 0)
    sin_venta = df["ventas_piezas"] <= 0

    df["_racha_raw"] = sin_venta.astype(int)
    df["_perdida_dia_raw"] = np.where(sin_venta & tiene_historial, df["promedio_diario_venta"].fillna(0.0), 0.0)

    seg_key = key + ["_segmento"]
    df["dias_racha_sin_venta"] = df.groupby(seg_key)["_racha_raw"].cumsum()
    df["perdida_piezas_dia"] = df["_perdida_dia_raw"]
    df["perdida_piezas_acumulada"] = df.groupby(seg_key)["_perdida_dia_raw"].cumsum()

    tipo = np.full(len(df), "normal", dtype=object)
    # inventario negativo ocurre en la data real de CheLink (ajustes/tiempos de
    # captura); se trata igual que 0 -- no hay pieza física disponible.
    es_oos = sin_venta & tiene_historial & (df["inventario_piezas"] <= 0)
    es_pi_candidato = sin_venta & tiene_historial & (df["inventario_piezas"] > 0)
    tipo[es_oos.to_numpy()] = "OOS"
    tipo[(es_pi_candidato & (df["perdida_piezas_acumulada"] >= umbral_pi)).to_numpy()] = "PI"
    tipo[(es_pi_candidato & (df["perdida_piezas_acumulada"] < umbral_pi)).to_numpy()] = "en_observacion"
    df["tipo_incidencia"] = tipo

    return df.drop(columns=["_segmento", "_racha_raw", "_perdida_dia_raw"])


def calcular_alertas(engine: Engine, ventana_dias: int | None = None) -> int:
    create_all(engine)
    umbral_pi = _get_config(engine, "umbral_pi_piezas", UMBRAL_PI_PIEZAS_DEFAULT)
    ventana_dias = ventana_dias or int(
        _get_config(engine, "ventana_alertas_dias", VENTANA_ALERTAS_DIAS_DEFAULT)
    )

    with engine.connect() as conn:
        fecha_max_row = conn.execute(text("SELECT MAX(fecha) FROM ventas_inventario_diario")).fetchone()
    if not fecha_max_row or fecha_max_row[0] is None:
        return 0
    fecha_max = pd.to_datetime(fecha_max_row[0]).date()
    fecha_corte = fecha_max - dt.timedelta(days=ventana_dias)
    fecha_lookback = fecha_corte - dt.timedelta(days=LOOKBACK_EXTRA_DIAS)

    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT fecha, num_tienda, sku, ventas_piezas, inventario_piezas, "
                "ventas_pesos AS ventas_pesos_reportado, inventario_pesos AS inventario_pesos_reportado "
                "FROM ventas_inventario_diario WHERE fecha >= :f ORDER BY num_tienda, sku, fecha"
            ),
            conn,
            params={"f": fecha_lookback},
        )
    if df.empty:
        return 0

    df["fecha"] = pd.to_datetime(df["fecha"])
    df["ventas_piezas"] = df["ventas_piezas"].fillna(0.0)
    df["inventario_piezas"] = df["inventario_piezas"].fillna(0.0)

    precios = _precio_lookup(engine)

    alertas = _clasificar_todo(df, umbral_pi)
    alertas["reportado"] = True
    alertas["fecha"] = alertas["fecha"].dt.date
    alertas = alertas[alertas["fecha"] >= fecha_corte].copy()

    # pesos = piezas x precio unitario vigente en esa fecha (as-of join por sku)
    alertas["precio_unitario"] = precio_por_fecha(alertas["fecha"], alertas["sku"], precios).to_numpy()
    alertas["perdida_pesos_dia"] = alertas["perdida_piezas_dia"] * alertas["precio_unitario"].fillna(0)
    alertas["perdida_pesos_acumulada"] = alertas["perdida_piezas_acumulada"] * alertas["precio_unitario"].fillna(0)
    # preferimos el monto tal como lo reportó la fuente (chelink); si no vino
    # (p.ej. backfill de dataverse, que no trae pesos), lo estimamos con el
    # precio unitario vigente.
    alertas["ventas_pesos"] = alertas["ventas_pesos_reportado"].fillna(
        alertas["ventas_piezas"] * alertas["precio_unitario"]
    )
    alertas["inventario_pesos"] = alertas["inventario_pesos_reportado"].fillna(
        alertas["inventario_piezas"] * alertas["precio_unitario"]
    )
    alertas["pronostico_piezas"] = np.nan
    alertas["pronostico_pesos"] = np.nan
    alertas["desempeno_vs_pronostico"] = "sin_pronostico"
    alertas = alertas.drop(columns=["precio_unitario", "ventas_pesos_reportado", "inventario_pesos_reportado"])

    cols = [c.name for c in alertas_diarias.columns]
    alertas = alertas[cols]

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alertas_diarias WHERE fecha >= :f"), {"f": fecha_corte})
    n = upsert_dataframe(engine, "alertas_diarias", alertas, ["fecha", "num_tienda", "sku"])
    return n
