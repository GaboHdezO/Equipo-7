# Conectar Power BI a la misma base

El motor de reglas (OOS, Phantom Inventory, venta perdida, pronóstico) corre
una sola vez en Python y escribe sus resultados en Postgres. Power BI no
recalcula nada: solo lee las tablas/vistas ya calculadas. Así el dashboard de
Streamlit y Power BI siempre muestran exactamente lo mismo.

## 1. Crear las vistas (una sola vez)

En Supabase: **SQL Editor** → pega el contenido de `vistas_powerbi.sql` de
esta carpeta → Run. Esto crea `dim_tiendas`, `dim_productos` y
`hechos_alertas` (esta última ya trae todo enriquecido: estado, ciudad,
nielsen, área, categoría, descripción, etc., junto con las columnas de
OOS/PI/venta perdida/pronóstico).

## 2. Conectar Power BI Desktop

1. Obtén el host, usuario y contraseña desde Supabase: **Project Settings →
   Database → Connection parameters** (no la cadena URI completa, aquí Power
   BI pide los campos por separado).
2. Power BI Desktop → **Obtener datos → Base de datos → PostgreSQL**.
3. Servidor: `db.<tu-proyecto>.supabase.co`, Base de datos: `postgres`.
4. Modo de conectividad de datos: **DirectQuery** si quieres que se actualice
   solo con cada carga (recomendado), o **Import** si prefieres refrescar
   manualmente.
5. Selecciona las vistas `hechos_alertas`, `dim_tiendas`, `dim_productos`.

## 3. Medidas DAX sugeridas

```dax
Días OOS = CALCULATE(COUNTROWS(hechos_alertas), hechos_alertas[tipo_incidencia] = "OOS")

Días PI = CALCULATE(COUNTROWS(hechos_alertas), hechos_alertas[tipo_incidencia] = "PI")

Venta perdida (piezas) = SUM(hechos_alertas[perdida_piezas_dia])

Venta perdida ($) = SUM(hechos_alertas[perdida_pesos_dia])

Disponibilidad % =
VAR Total = COUNTROWS(hechos_alertas)
VAR SinOOS = CALCULATE(COUNTROWS(hechos_alertas), hechos_alertas[tipo_incidencia] <> "OOS")
RETURN DIVIDE(SinOOS, Total)

OOS % = DIVIDE([Días OOS], COUNTROWS(hechos_alertas))

PI % = DIVIDE([Días PI], COUNTROWS(hechos_alertas))

Tiendas con incidencia =
CALCULATE(
    DISTINCTCOUNT(hechos_alertas[num_tienda]),
    hechos_alertas[tipo_incidencia] IN {"OOS", "PI"}
)
```

## 4. Visuales sugeridos (igual que en el dashboard de Streamlit)

- **Gráfica de columnas + líneas** (eje X = fecha, con jerarquía
  Año/Mes/Semana/Día): columnas = `inventario_piezas` (promedio), línea =
  `ventas_piezas` (suma), segunda línea = `pronostico_piezas` (suma). Repetir
  con las columnas `_pesos` para la vista en monto.
- Segunda gráfica igual pero con `OOS %`, `PI %` como líneas y
  `Disponibilidad %` como columnas.
- **Top N tiendas**: gráfica de barras horizontales sobre `dim_tiendas`,
  ordenada por `[Días OOS]`, `[Días PI]` o `[Venta perdida ($)]`, con un
  parámetro "Top N" (Power BI: *What-if parameter*) conectado a un filtro de
  TopN visual-level.
- **Filtros**: segmentaciones (slicers) por `estado`, `nielsen`, `area`,
  `nombre_tienda`, `categoria`, `upc`/`descripcion`.
- **Exportar**: cualquier tabla/visual en Power BI se exporta a
  Excel/CSV con el botón nativo "Exportar datos" (clic derecho sobre el
  visual), sin necesidad de configurar nada adicional.

## Nota sobre acceso desde el teléfono

A diferencia del dashboard de Streamlit (que es una URL pública que abre en
cualquier navegador sin licencias), para que alguien vea este reporte de
Power BI en su celular sin instalar nada adicional necesita: (a) la app móvil
gratuita de Power BI, y (b) que tenga una licencia Power BI Pro (o que la
organización tenga capacidad Premium/Fabric) para poder ver contenido
compartido desde el servicio de Power BI. Sin eso, el reporte solo se puede
abrir localmente en la computadora donde está instalado Power BI Desktop.
