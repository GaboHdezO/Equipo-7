"""Dashboard de Inventario y Ventas — Chedraui.

Carga de archivos, motor de reglas (OOS / Phantom Inventory / venta perdida)
y pronóstico, todo en una sola página. Se conecta a SQLite local por defecto
o a Postgres si existe la variable de entorno DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.colores import CATEGORICO, ESTATUS, TINTA
from pipeline.db import get_engine, is_postgres
from pipeline.exportar import a_csv_bytes, a_xlsx_bytes
from pipeline.ingest import load_alcances, load_chelink, load_claves, load_dataverse
from pipeline.queries import alertas_enriquecidas, carga_log_reciente, dim_productos, dim_tiendas
from pipeline.run_pipeline import recalcular_todo
from pipeline.schema import create_all
from sqlalchemy import text

st.set_page_config(page_title="Chedraui · Inventario y Ventas", layout="wide", page_icon="📦")

engine = get_engine()
create_all(engine)  # crea las tablas si la base está vacía (primer arranque contra una BD nueva)

if "cache_token" not in st.session_state:
    st.session_state.cache_token = 0


def _tras_carga(mensaje: str, filas: int) -> None:
    st.session_state.cache_token += 1
    st.sidebar.success(f"{mensaje}: {filas} filas")


def _guardar_temp(uploaded_file, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        return tmp.name


@st.cache_data(show_spinner=False)
def _cargar_alertas(token: int) -> pd.DataFrame:
    return alertas_enriquecidas(engine)


def _get_config(clave: str, default: float) -> float:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT valor FROM config_parametros WHERE clave = :c"), {"c": clave}).fetchone()
    return float(row[0]) if row else default


def _set_config(clave: str, valor: float) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM config_parametros WHERE clave = :c"), {"c": clave})
        conn.execute(text("INSERT INTO config_parametros (clave, valor) VALUES (:c, :v)"), {"c": clave, "v": str(valor)})


# ----------------------------------------------------------------------------
# Sidebar: carga de archivos
# ----------------------------------------------------------------------------
st.sidebar.title("Carga de archivos")
st.sidebar.caption(
    "Conectado a " + ("Postgres (compartido)" if is_postgres() else "SQLite local (solo esta sesión)")
)
if is_postgres():
    st.sidebar.caption(f"🔎 Host: `{engine.url.host}` · Base: `{engine.url.database}` · Usuario: `{engine.url.username}`")
    try:
        with engine.connect() as _diag_conn:
            _esquema_activo = _diag_conn.execute(text("SELECT current_schema()")).scalar()
            _tablas = _diag_conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() ORDER BY table_name"
                )
            ).fetchall()
        st.sidebar.caption(f"📋 Esquema activo: `{_esquema_activo}`")
        st.sidebar.caption("Tablas visibles: " + (", ".join(t[0] for t in _tablas) if _tablas else "(ninguna)"))
    except Exception as _diag_err:
        st.sidebar.error(f"No se pudo listar tablas: {_diag_err}")

with st.sidebar.expander("📋 Catálogo de Claves", expanded=False):
    f_claves = st.file_uploader("Claves.xlsx", type=["xlsx"], key="up_claves")
    v_claves = st.date_input("Vigente desde", value=dt.date.today(), key="v_claves")
    if st.button("Cargar catálogo", key="btn_claves", disabled=f_claves is None):
        with st.spinner("Cargando catálogo y recalculando..."):
            tmp = _guardar_temp(f_claves, ".xlsx")
            n = load_claves(engine, tmp, v_claves)
            recalcular_todo(engine)
        _tras_carga("Catálogo cargado", n)

with st.sidebar.expander("🏬 Alcances (tienda-SKU)", expanded=False):
    f_alcances = st.file_uploader("Alcances_....xlsx", type=["xlsx"], key="up_alcances")
    v_alcances = st.date_input("Vigente desde", value=dt.date.today(), key="v_alcances")
    if st.button("Cargar alcances", key="btn_alcances", disabled=f_alcances is None):
        with st.spinner("Cargando alcances y recalculando..."):
            tmp = _guardar_temp(f_alcances, ".xlsx")
            n = load_alcances(engine, tmp, v_alcances)
            recalcular_todo(engine)
        _tras_carga("Alcances cargados", n)

with st.sidebar.expander("📅 CheLink diario", expanded=True):
    f_chelink = st.file_uploader(
        "CheLink_YYYYMMDD.txt (puedes soltar varios días a la vez)",
        type=["txt"],
        accept_multiple_files=True,
        key="up_chelink",
    )
    if st.button("Cargar CheLink", key="btn_chelink", disabled=not f_chelink):
        with st.spinner("Cargando CheLink y recalculando alertas..."):
            total = 0
            for f in f_chelink:
                tmp = _guardar_temp(f, ".txt")
                total += load_chelink(engine, tmp)
            recalcular_todo(engine)
        _tras_carga("CheLink cargado", total)

with st.sidebar.expander("🗄️ Histórico Dataverse (una sola vez)", expanded=False):
    st.caption("Backfill histórico. Úsalo solo la primera vez; CheLink lo reemplaza hacia adelante.")
    f_dv = st.file_uploader("Dataverse.parquet", type=["parquet"], key="up_dv")
    if st.button("Cargar histórico", key="btn_dv", disabled=f_dv is None):
        with st.spinner("Cargando histórico (puede tardar varios minutos)..."):
            tmp = _guardar_temp(f_dv, ".parquet")
            n = load_dataverse(engine, tmp)
            recalcular_todo(engine)
        _tras_carga("Histórico cargado", n)

st.sidebar.divider()
st.sidebar.subheader("Parámetros del motor de reglas")
umbral_actual = _get_config("umbral_pi_piezas", 1.5)
ventana_actual = _get_config("ventana_alertas_dias", 400)
umbral_nuevo = st.sidebar.number_input("Umbral Phantom Inventory (piezas)", value=float(umbral_actual), step=0.1, min_value=0.1)
ventana_nueva = st.sidebar.number_input("Ventana de recálculo (días)", value=int(ventana_actual), step=30, min_value=30)
if st.sidebar.button("Guardar y recalcular"):
    with st.spinner("Recalculando con los nuevos parámetros..."):
        _set_config("umbral_pi_piezas", umbral_nuevo)
        _set_config("ventana_alertas_dias", ventana_nueva)
        recalcular_todo(engine)
    st.session_state.cache_token += 1
    st.sidebar.success("Parámetros guardados y alertas recalculadas")

with st.sidebar.expander("Registro de cargas", expanded=False):
    st.dataframe(carga_log_reciente(engine, 15), hide_index=True, use_container_width=True)

# ----------------------------------------------------------------------------
# Datos
# ----------------------------------------------------------------------------
st.title("📦 Inventario, OOS, Phantom Inventory y Venta Perdida")

df = _cargar_alertas(st.session_state.cache_token)

if df.empty:
    st.info("Todavía no hay datos cargados. Usa el panel de la izquierda para cargar Claves, Alcances, "
            "el histórico de Dataverse (una sola vez) y los archivos CheLink del día.")
    st.stop()

# ----------------------------------------------------------------------------
# Filtros
# ----------------------------------------------------------------------------
st.subheader("Filtros")
c1, c2, c3, c4, c5 = st.columns(5)
estados = c1.multiselect("Estado", sorted(df["estado"].dropna().unique()))
nielsen = c2.multiselect("Área Nielsen", sorted(df["nielsen"].dropna().unique()))
tiendas = c3.multiselect(
    "Tienda", sorted(df["nombre_tienda"].dropna().unique()), help="Puedes escribir para buscar"
)
categorias = c4.multiselect("Categoría", sorted(df["categoria"].dropna().unique()))
df["producto_label"] = df["upc"].astype(str) + " · " + df["descripcion"].fillna("")
productos = c5.multiselect("UPC / Producto", sorted(df["producto_label"].dropna().unique()))

c6, c7 = st.columns([2, 1])
fecha_min, fecha_max = df["fecha"].min().date(), df["fecha"].max().date()
rango = c6.date_input("Rango de fechas", value=(fecha_min, fecha_max), min_value=fecha_min, max_value=fecha_max)
granularidad = c7.selectbox("Granularidad", ["Día", "Semana", "Mes", "Año"], index=0)

df_f = df.copy()
if estados:
    df_f = df_f[df_f["estado"].isin(estados)]
if nielsen:
    df_f = df_f[df_f["nielsen"].isin(nielsen)]
if tiendas:
    df_f = df_f[df_f["nombre_tienda"].isin(tiendas)]
if categorias:
    df_f = df_f[df_f["categoria"].isin(categorias)]
if productos:
    df_f = df_f[df_f["producto_label"].isin(productos)]
if isinstance(rango, tuple) and len(rango) == 2:
    df_f = df_f[(df_f["fecha"].dt.date >= rango[0]) & (df_f["fecha"].dt.date <= rango[1])]

if df_f.empty:
    st.warning("No hay datos para esta combinación de filtros.")
    st.stop()

# ----------------------------------------------------------------------------
# KPIs
# ----------------------------------------------------------------------------
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Días-SKU en OOS", f"{(df_f['tipo_incidencia'] == 'OOS').sum():,}")
k2.metric("Días-SKU en PI", f"{(df_f['tipo_incidencia'] == 'PI').sum():,}")
k3.metric("Venta perdida (piezas)", f"{df_f['perdida_piezas_dia'].sum():,.0f}")
k4.metric("Venta perdida ($)", f"${df_f['perdida_pesos_dia'].sum():,.0f}")
tiendas_afectadas = df_f.loc[df_f["tipo_incidencia"].isin(["OOS", "PI"]), "num_tienda"].nunique()
k5.metric("Tiendas con incidencia", f"{tiendas_afectadas:,}")

# ----------------------------------------------------------------------------
# Agregación por granularidad
# ----------------------------------------------------------------------------
FREQ = {"Día": "D", "Semana": "W-MON", "Mes": "MS", "Año": "YS"}[granularidad]
grp = df_f.groupby(pd.Grouper(key="fecha", freq=FREQ))

serie = grp.agg(
    inventario_piezas=("inventario_piezas", "mean"),
    ventas_piezas=("ventas_piezas", "sum"),
    pronostico_piezas=("pronostico_piezas", "sum"),
    inventario_pesos=("inventario_pesos", "mean"),
    ventas_pesos=("ventas_pesos", "sum"),
    pronostico_pesos=("pronostico_pesos", "sum"),
).reset_index()

tipo_counts = df_f.groupby([pd.Grouper(key="fecha", freq=FREQ), "tipo_incidencia"]).size().unstack(fill_value=0)
total_reportado = tipo_counts.sum(axis=1)
disponibilidad_pct = ((total_reportado - tipo_counts.get("OOS", 0)) / total_reportado * 100).reset_index(drop=True)
oos_pct = (tipo_counts.get("OOS", 0) / total_reportado * 100).reset_index(drop=True)
pi_pct = (tipo_counts.get("PI", 0) / total_reportado * 100).reset_index(drop=True)

# ----------------------------------------------------------------------------
# Gráfica 1: unidades
# ----------------------------------------------------------------------------
st.subheader("Inventario, venta y pronóstico — unidades")
fig1 = go.Figure()
fig1.add_bar(x=serie["fecha"], y=serie["inventario_piezas"], name="Inventario disponible (prom., piezas)", marker_color=CATEGORICO["inventario"])
fig1.add_scatter(x=serie["fecha"], y=serie["ventas_piezas"], name="Venta real (piezas)", mode="lines", line=dict(color=CATEGORICO["venta_real"], width=2))
fig1.add_scatter(x=serie["fecha"], y=serie["pronostico_piezas"], name="Pronóstico (piezas)", mode="lines", line=dict(color=CATEGORICO["pronostico"], width=2, dash="dash"))
fig1.update_layout(
    template="plotly_white", hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02),
    yaxis_title="Piezas", xaxis_title="Fecha", plot_bgcolor=TINTA["superficie"], paper_bgcolor=TINTA["superficie"],
    margin=dict(t=10),
)
st.plotly_chart(fig1, use_container_width=True)

# ----------------------------------------------------------------------------
# Gráfica 2: monto
# ----------------------------------------------------------------------------
st.subheader("Inventario, venta y pronóstico — monto ($)")
fig2 = go.Figure()
fig2.add_bar(x=serie["fecha"], y=serie["inventario_pesos"], name="Inventario disponible (prom., $)", marker_color=CATEGORICO["inventario"])
fig2.add_scatter(x=serie["fecha"], y=serie["ventas_pesos"], name="Venta real ($)", mode="lines", line=dict(color=CATEGORICO["venta_real"], width=2))
fig2.add_scatter(x=serie["fecha"], y=serie["pronostico_pesos"], name="Pronóstico ($)", mode="lines", line=dict(color=CATEGORICO["pronostico"], width=2, dash="dash"))
fig2.update_layout(
    template="plotly_white", hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02),
    yaxis_title="Pesos", xaxis_title="Fecha", plot_bgcolor=TINTA["superficie"], paper_bgcolor=TINTA["superficie"],
    margin=dict(t=10),
)
st.plotly_chart(fig2, use_container_width=True)

# ----------------------------------------------------------------------------
# Gráfica 3: disponibilidad / OOS / PI
# ----------------------------------------------------------------------------
st.subheader("Disponibilidad, OOS y Phantom Inventory (% de combinaciones tienda-SKU)")
fig3 = go.Figure()
fig3.add_bar(x=serie["fecha"], y=disponibilidad_pct, name="Disponibilidad (%)", marker_color=ESTATUS["good"])
fig3.add_scatter(x=serie["fecha"], y=oos_pct, name="OOS (%)", mode="lines", line=dict(color=ESTATUS["critical"], width=2))
fig3.add_scatter(x=serie["fecha"], y=pi_pct, name="Phantom Inventory (%)", mode="lines", line=dict(color=ESTATUS["warning"], width=2))
fig3.update_layout(
    template="plotly_white", hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02),
    yaxis_title="% de combinaciones tienda-SKU", xaxis_title="Fecha", plot_bgcolor=TINTA["superficie"],
    paper_bgcolor=TINTA["superficie"], margin=dict(t=10),
)
st.plotly_chart(fig3, use_container_width=True)

# ----------------------------------------------------------------------------
# Top-N
# ----------------------------------------------------------------------------
st.subheader("Top tiendas")
n_top = st.number_input("Número de tiendas a mostrar (N)", min_value=3, max_value=100, value=10, step=1)

t1, t2, t3 = st.columns(3)

top_oos = (
    df_f[df_f["tipo_incidencia"] == "OOS"].groupby(["num_tienda", "nombre_tienda"]).size()
    .nlargest(n_top).reset_index(name="dias_oos")
)
with t1:
    st.caption("Mayor cantidad de días-SKU en OOS")
    fig_oos = go.Figure(go.Bar(y=top_oos["nombre_tienda"], x=top_oos["dias_oos"], orientation="h", marker_color=ESTATUS["critical"]))
    fig_oos.update_layout(template="plotly_white", yaxis=dict(autorange="reversed"), margin=dict(t=10), height=350)
    st.plotly_chart(fig_oos, use_container_width=True)

top_pi = (
    df_f[df_f["tipo_incidencia"] == "PI"].groupby(["num_tienda", "nombre_tienda"]).size()
    .nlargest(n_top).reset_index(name="dias_pi")
)
with t2:
    st.caption("Mayor cantidad de días-SKU en Phantom Inventory")
    fig_pi = go.Figure(go.Bar(y=top_pi["nombre_tienda"], x=top_pi["dias_pi"], orientation="h", marker_color=ESTATUS["warning"]))
    fig_pi.update_layout(template="plotly_white", yaxis=dict(autorange="reversed"), margin=dict(t=10), height=350)
    st.plotly_chart(fig_pi, use_container_width=True)

top_perdida = (
    df_f.groupby(["num_tienda", "nombre_tienda"])["perdida_pesos_dia"].sum()
    .nlargest(n_top).reset_index(name="venta_perdida_pesos")
)
with t3:
    st.caption("Mayor venta perdida estimada ($)")
    fig_perdida = go.Figure(go.Bar(y=top_perdida["nombre_tienda"], x=top_perdida["venta_perdida_pesos"], orientation="h", marker_color=CATEGORICO["inventario"]))
    fig_perdida.update_layout(template="plotly_white", yaxis=dict(autorange="reversed"), margin=dict(t=10), height=350)
    st.plotly_chart(fig_perdida, use_container_width=True)

# ----------------------------------------------------------------------------
# Desempeño vs pronóstico (estado más reciente por combinación)
# ----------------------------------------------------------------------------
st.subheader("Comportamiento de venta vs. pronóstico (estado más reciente por combinación)")
ultimo = df_f.sort_values("fecha").groupby(["num_tienda", "sku"]).tail(1)
resumen_desempeno = ultimo["desempeno_vs_pronostico"].value_counts().reindex(
    ["sobrevendiendo", "normal", "bajo_pronostico", "sin_pronostico"], fill_value=0
)
d1, d2, d3, d4 = st.columns(4)
d1.metric("Sobrevendiendo", int(resumen_desempeno.get("sobrevendiendo", 0)))
d2.metric("Normal", int(resumen_desempeno.get("normal", 0)))
d3.metric("Bajo pronóstico", int(resumen_desempeno.get("bajo_pronostico", 0)))
d4.metric("Sin pronóstico (historial insuficiente)", int(resumen_desempeno.get("sin_pronostico", 0)))

# ----------------------------------------------------------------------------
# Descargas
# ----------------------------------------------------------------------------
st.subheader("Descargas")
e1, e2, e3 = st.columns(3)

activas = df_f[df_f["tipo_incidencia"].isin(["OOS", "PI", "en_observacion"])].sort_values(
    "perdida_piezas_acumulada", ascending=False
)
cols_activas = [
    "fecha", "num_tienda", "nombre_tienda", "estado", "nielsen", "sku", "descripcion", "categoria",
    "tipo_incidencia", "dias_racha_sin_venta", "inventario_piezas", "promedio_diario_venta",
    "perdida_piezas_acumulada", "perdida_pesos_acumulada",
]
with e1:
    st.caption("Alertas activas (OOS / PI / en observación)")
    st.download_button("Descargar CSV", a_csv_bytes(activas[cols_activas]), "alertas_activas.csv", "text/csv")
    st.download_button("Descargar Excel", a_xlsx_bytes(activas[cols_activas], "alertas"), "alertas_activas.xlsx")

with e2:
    st.caption("Detalle completo filtrado")
    st.download_button("Descargar CSV", a_csv_bytes(df_f), "detalle_filtrado.csv", "text/csv")
    st.download_button("Descargar Excel", a_xlsx_bytes(df_f, "detalle"), "detalle_filtrado.xlsx")

with e3:
    st.caption(f"Top {n_top} tiendas por venta perdida")
    st.download_button("Descargar CSV", a_csv_bytes(top_perdida), "top_venta_perdida.csv", "text/csv")
    st.download_button("Descargar Excel", a_xlsx_bytes(top_perdida, "top_perdida"), "top_venta_perdida.xlsx")

with st.expander("Ver detalle completo filtrado en pantalla"):
    st.dataframe(df_f, use_container_width=True, hide_index=True)
