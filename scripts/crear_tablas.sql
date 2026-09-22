-- Crea el esquema completo directamente en Postgres/Supabase.
-- Usar solo si la app no logra crearlo sola (create_all) contra el pooler.
-- Supabase: SQL Editor -> New query -> pegar todo -> Run. Es seguro volver a
-- correrlo (IF NOT EXISTS en cada tabla).

CREATE TABLE IF NOT EXISTS catalogo_claves (
	vigente_desde DATE NOT NULL,
	sku INTEGER NOT NULL,
	upc VARCHAR NOT NULL,
	descripcion VARCHAR,
	categoria VARCHAR,
	subcategoria VARCHAR,
	resurtible VARCHAR,
	empaque INTEGER,
	e3 VARCHAR,
	precio_unitario FLOAT,
	costo_unitario FLOAT,
	PRIMARY KEY (vigente_desde, sku)
);

CREATE TABLE IF NOT EXISTS alcances (
	vigente_desde DATE NOT NULL,
	num_tienda INTEGER NOT NULL,
	sku INTEGER NOT NULL,
	formato VARCHAR,
	sucursal VARCHAR,
	nombre_tienda VARCHAR,
	estado VARCHAR,
	ciudad VARCHAR,
	nielsen VARCHAR,
	area VARCHAR,
	alcance INTEGER,
	PRIMARY KEY (vigente_desde, num_tienda, sku)
);

CREATE TABLE IF NOT EXISTS ventas_inventario_diario (
	fecha DATE NOT NULL,
	num_tienda INTEGER NOT NULL,
	sku INTEGER NOT NULL,
	upc VARCHAR,
	ventas_piezas FLOAT,
	ventas_pesos FLOAT,
	inventario_piezas FLOAT,
	inventario_pesos FLOAT,
	fuente VARCHAR,
	cargado_en TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (fecha, num_tienda, sku)
);

CREATE TABLE IF NOT EXISTS alertas_diarias (
	fecha DATE NOT NULL,
	num_tienda INTEGER NOT NULL,
	sku INTEGER NOT NULL,
	reportado BOOLEAN,
	ventas_piezas FLOAT,
	ventas_pesos FLOAT,
	inventario_piezas FLOAT,
	inventario_pesos FLOAT,
	promedio_diario_venta FLOAT,
	tipo_incidencia VARCHAR,
	dias_racha_sin_venta INTEGER,
	perdida_piezas_dia FLOAT,
	perdida_pesos_dia FLOAT,
	perdida_piezas_acumulada FLOAT,
	perdida_pesos_acumulada FLOAT,
	pronostico_piezas FLOAT,
	pronostico_pesos FLOAT,
	desempeno_vs_pronostico VARCHAR,
	PRIMARY KEY (fecha, num_tienda, sku)
);

CREATE TABLE IF NOT EXISTS carga_log (
	id SERIAL NOT NULL,
	tipo_archivo VARCHAR,
	nombre_archivo VARCHAR,
	filas_procesadas INTEGER,
	fecha_proceso TIMESTAMP WITHOUT TIME ZONE,
	notas VARCHAR,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS config_parametros (
	clave VARCHAR NOT NULL,
	valor VARCHAR,
	PRIMARY KEY (clave)
);
