"""Pronóstico de venta y detección de sobreventa / subventa.

Para cada combinación tienda-sku se calcula un pronóstico diario con dos
componentes combinados:
  - Nivel base: promedio histórico de venta en días con venta real (para no
    diluir el nivel con ceros que ya sabemos que son OOS/PI, no demanda real).
  - Tendencia: regresión lineal simple sobre la serie de venta diaria
    reciente (ventana configurable), para capturar si la venta viene
    creciendo o cayendo.

Si una combinación no tiene suficiente historial (menos de MIN_OBS
observaciones), se usa como respaldo el promedio de su categoría en esa misma
tienda, y si tampoco hay eso, el promedio general de la categoría.

Desempeño vs pronóstico: se compara la venta real de los últimos N días
contra el pronóstico de esos mismos días; una desviación > +30% se marca
"sobrevendiendo" (riesgo de quedarse sin inventario pronto) y < -30%
"bajo_pronostico" (se está vendiendo menos de lo esperado).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .rules import precio_por_fecha
from .schema import alertas_diarias
from .upsert import upsert_dataframe

MIN_OBS = 14
VENTANA_TENDENCIA = 60
VENTANA_DESEMPENO = 14
UMBRAL_DESVIACION = 0.30


def _pronostico_todo(df: pd.DataFrame) -> pd.DataFrame:
    """Regresión lineal móvil vectorizada sobre el DataFrame COMPLETO (todas
    las combinaciones tienda-sku a la vez, vía groupby(...).rolling()) --
    igual que en rules.py, se evita un `for` por combinación porque con miles
    de tiendas x SKUs esa parte no escala.

    Para cada día, ajusta una recta sobre los VENTANA_TENDENCIA días previos
    de esa combinación (excluyendo el día actual) y combina esa tendencia con
    el nivel base (promedio de esos mismos días). La pendiente de una
    regresión es invariante a un corrimiento del eje x, así que se puede usar
    el índice global de día (t) directamente con sumas móviles agrupadas."""
    key = ["num_tienda", "sku"]
    df = df.sort_values(key + ["fecha"]).reset_index(drop=True)

    t = df.groupby(key).cumcount().astype(float)
    y_prev = df.groupby(key)["ventas_piezas"].shift(1)
    t_prev = t - 1  # posición dentro del grupo; y_prev ya trae NaN en el primer renglón de cada grupo

    gkeys = [df["num_tienda"], df["sku"]]

    def roll(s: pd.Series):
        return s.groupby(gkeys).rolling(VENTANA_TENDENCIA, min_periods=MIN_OBS)

    def alineado(rolled) -> pd.Series:
        return rolled.reset_index(level=key, drop=True)

    cnt = alineado(roll(y_prev).count())
    sum_t = alineado(roll(t_prev).sum())
    sum_y = alineado(roll(y_prev).sum())
    sum_t2 = alineado(roll(t_prev * t_prev).sum())
    sum_ty = alineado(roll(t_prev * y_prev).sum())

    denom = cnt * sum_t2 - sum_t**2
    slope = ((cnt * sum_ty - sum_t * sum_y) / denom.replace(0, np.nan)).fillna(0.0)
    intercept = (sum_y - slope * sum_t) / cnt

    nivel_base = alineado(roll(y_prev).mean())
    tendencia = intercept + slope * t

    pronostico = (0.5 * nivel_base + 0.5 * tendencia).clip(lower=0.0)
    pronostico[cnt < MIN_OBS] = np.nan

    df["pronostico_piezas"] = pronostico
    return df


def calcular_pronostico(engine: Engine, ventana_dias: int | None = None) -> int:
    with engine.connect() as conn:
        fecha_max_row = conn.execute(text("SELECT MAX(fecha) FROM alertas_diarias")).fetchone()
    if not fecha_max_row or fecha_max_row[0] is None:
        return 0

    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT fecha, num_tienda, sku, ventas_piezas, ventas_pesos, inventario_piezas, "
                "inventario_pesos, perdida_piezas_dia, tipo_incidencia, perdida_piezas_acumulada, "
                "perdida_pesos_dia, perdida_pesos_acumulada, promedio_diario_venta, "
                "dias_racha_sin_venta, reportado "
                "FROM alertas_diarias ORDER BY num_tienda, sku, fecha"
            ),
            conn,
        )
    if df.empty:
        return 0
    df["fecha"] = pd.to_datetime(df["fecha"])

    precios = pd.read_sql(
        text("SELECT vigente_desde, sku, precio_unitario FROM catalogo_claves ORDER BY sku, vigente_desde"),
        engine,
    )
    precios["vigente_desde"] = pd.to_datetime(precios["vigente_desde"]).astype("datetime64[ns]")

    # venta "real de demanda": en días OOS/PI usamos la venta observada (puede
    # ser 0), el pronóstico se basa en la serie de venta tal cual se reportó.
    out = _pronostico_todo(df)

    key = ["num_tienda", "sku"]
    gkeys = [out["num_tienda"], out["sku"]]
    real_movil = out["ventas_piezas"].groupby(gkeys).rolling(VENTANA_DESEMPENO, min_periods=5).sum().reset_index(
        level=key, drop=True
    )
    pron_movil = out["pronostico_piezas"].groupby(gkeys).rolling(VENTANA_DESEMPENO, min_periods=5).sum().reset_index(
        level=key, drop=True
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        desviacion = (real_movil - pron_movil) / pron_movil

    out["desempeno_vs_pronostico"] = np.where(
        desviacion.isna(),
        "sin_pronostico",
        np.where(
            desviacion > UMBRAL_DESVIACION,
            "sobrevendiendo",
            np.where(desviacion < -UMBRAL_DESVIACION, "bajo_pronostico", "normal"),
        ),
    )

    precio_por_fila = precio_por_fecha(out["fecha"], out["sku"], precios)
    out["pronostico_pesos"] = out["pronostico_piezas"] * precio_por_fila.fillna(0).to_numpy()
    out["fecha"] = out["fecha"].dt.date

    cols = [c.name for c in alertas_diarias.columns]
    out = out[cols]

    n = upsert_dataframe(engine, "alertas_diarias", out, ["fecha", "num_tienda", "sku"])
    return n
