"""Definición de tablas (portable entre SQLite local y Postgres/Supabase).

Se usa SQLAlchemy Core (no raw SQL) precisamente para que el mismo código
cree las tablas correctas sin importar el motor.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

# --- Maestros versionados -----------------------------------------------
# Cada carga de Claves.xlsx o Alcances.xlsx crea una versión nueva con su
# fecha de vigencia; nunca se borra la anterior, así los cálculos históricos
# usan el catálogo/alcance que estaba vigente en cada fecha.

catalogo_claves = Table(
    "catalogo_claves",
    metadata,
    Column("vigente_desde", Date, primary_key=True),
    Column("sku", Integer, primary_key=True),
    Column("upc", String, nullable=False),
    Column("descripcion", String),
    Column("categoria", String),
    Column("subcategoria", String),
    Column("resurtible", String),
    Column("empaque", Integer),
    Column("e3", String),
    Column("precio_unitario", Float),
    Column("costo_unitario", Float),
)

alcances = Table(
    "alcances",
    metadata,
    Column("vigente_desde", Date, primary_key=True),
    Column("num_tienda", Integer, primary_key=True),
    Column("sku", Integer, primary_key=True),
    Column("formato", String),
    Column("sucursal", String),
    Column("nombre_tienda", String),
    Column("estado", String),
    Column("ciudad", String),
    Column("nielsen", String),
    Column("area", String),
    Column("alcance", Integer),
)

# --- Hechos diarios --------------------------------------------------------
# Un renglón por (fecha, tienda, sku). fuente indica de dónde vino el dato
# ('dataverse' para el backfill histórico único, 'chelink' para la carga
# diaria en curso). Si un día llega por ambas fuentes, chelink gana porque es
# el feed operativo vigente.

ventas_inventario_diario = Table(
    "ventas_inventario_diario",
    metadata,
    Column("fecha", Date, primary_key=True),
    Column("num_tienda", Integer, primary_key=True),
    Column("sku", Integer, primary_key=True),
    Column("upc", String),
    Column("ventas_piezas", Float),
    Column("ventas_pesos", Float),
    Column("inventario_piezas", Float),
    Column("inventario_pesos", Float),
    Column("fuente", String),
    Column("cargado_en", DateTime),
)

# --- Resultado del motor de reglas (se recalcula completo en cada carga) --

alertas_diarias = Table(
    "alertas_diarias",
    metadata,
    Column("fecha", Date, primary_key=True),
    Column("num_tienda", Integer, primary_key=True),
    Column("sku", Integer, primary_key=True),
    Column("reportado", Boolean),
    Column("ventas_piezas", Float),
    Column("ventas_pesos", Float),
    Column("inventario_piezas", Float),
    Column("inventario_pesos", Float),
    Column("promedio_diario_venta", Float),
    Column("tipo_incidencia", String),  # normal | en_observacion | PI | OOS
    Column("dias_racha_sin_venta", Integer),
    Column("perdida_piezas_dia", Float),
    Column("perdida_pesos_dia", Float),
    Column("perdida_piezas_acumulada", Float),
    Column("perdida_pesos_acumulada", Float),
    Column("pronostico_piezas", Float),
    Column("pronostico_pesos", Float),
    Column("desempeno_vs_pronostico", String),  # sobrevendiendo | bajo_pronostico | normal | sin_pronostico
)

carga_log = Table(
    "carga_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tipo_archivo", String),
    Column("nombre_archivo", String),
    Column("filas_procesadas", Integer),
    Column("fecha_proceso", DateTime),
    Column("notas", String),
)

config_parametros = Table(
    "config_parametros",
    metadata,
    Column("clave", String, primary_key=True),
    Column("valor", String),
)


def create_all(engine):
    metadata.create_all(engine)
