-- Vistas para conectar Power BI directamente a Postgres/Supabase, con el
-- mismo modelo (dimensiones + hechos) que usa el dashboard de Streamlit.
-- Ejecutar una sola vez contra la base (Supabase: SQL Editor -> pegar y correr).
-- Se pueden volver a correr sin problema (CREATE OR REPLACE).

-- Dimensión tienda: una fila por tienda, tomada de la versión de Alcances
-- más reciente.
CREATE OR REPLACE VIEW dim_tiendas AS
SELECT DISTINCT ON (num_tienda)
    num_tienda, formato, sucursal, nombre_tienda, estado, ciudad, nielsen, area
FROM alcances
WHERE vigente_desde = (SELECT MAX(vigente_desde) FROM alcances)
ORDER BY num_tienda;

-- Dimensión producto: una fila por SKU, tomada de la versión de Claves más
-- reciente.
CREATE OR REPLACE VIEW dim_productos AS
SELECT sku, upc, descripcion, categoria, subcategoria, resurtible, empaque, e3,
       precio_unitario, costo_unitario
FROM catalogo_claves
WHERE vigente_desde = (SELECT MAX(vigente_desde) FROM catalogo_claves);

-- Hechos enriquecidos: la tabla de alertas ya trae todo lo que calcula el
-- motor de reglas (OOS, PI, venta perdida, pronóstico); aquí solo se le
-- pegan las dimensiones para que Power BI arme el modelo en estrella con un
-- solo "Get Data".
CREATE OR REPLACE VIEW hechos_alertas AS
SELECT
    a.*,
    t.formato, t.sucursal, t.nombre_tienda, t.estado, t.ciudad, t.nielsen, t.area,
    p.upc, p.descripcion, p.categoria, p.subcategoria
FROM alertas_diarias a
LEFT JOIN dim_tiendas t ON t.num_tienda = a.num_tienda
LEFT JOIN dim_productos p ON p.sku = a.sku;
