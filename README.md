# Dashboard de Inventario y Ventas — Chedraui

Detecta Out of Stock (OOS), Phantom Inventory (PI), venta perdida acumulada,
disponibilidad en anaquel y comportamiento de venta (real vs. pronóstico),
cruzando el catálogo de claves, los alcances por tienda y el feed diario de
CheLink. Corre como dashboard web (Streamlit, cualquier navegador/celular) y,
en paralelo, como reporte de Power BI conectado a la misma base de datos.

## Cómo está armado

```
pipeline/     motor de datos: ingesta, reglas OOS/PI, pronóstico, consultas
app/          dashboard Streamlit (lo que ve el usuario final)
powerbi/      vistas SQL + guía para conectar Power BI a la misma base
scripts/      utilidades sueltas
```

- **Base de datos**: SQLite local por defecto (cero configuración, para
  probar en tu máquina). En cuanto exista la variable de entorno
  `DATABASE_URL` (Postgres/Supabase), se usa esa automáticamente — mismo
  código, sin tocar nada.
- **Un solo motor de reglas**: corre en Python (`pipeline/rules.py` y
  `pipeline/forecast.py`), escribe su resultado en la tabla `alertas_diarias`,
  y tanto el dashboard de Streamlit como Power BI solo *leen* esa tabla. Así
  nunca hay dos definiciones de "qué es un OOS" corriendo por separado.

## Cómo correrlo en tu máquina

```bash
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

Abre `http://localhost:8501`, y desde el panel de la izquierda carga (en este
orden, la primera vez): Claves.xlsx → Alcances.xlsx → histórico de Dataverse
(una sola vez) → los CheLink diarios que tengas. Cada carga recalcula
automáticamente OOS/PI/pérdida/pronóstico.

## Ponerlo en línea para que cualquiera lo vea (sin instalar nada)

1. **Sube este repositorio a GitHub** (ya está listo para eso).
2. **Crea la base de datos compartida** en [supabase.com](https://supabase.com)
   (plan gratuito, sin tarjeta): crea un proyecto → **Project Settings →
   Database → Connection string** (pestaña URI, modo *Transaction pooler*) →
   copia esa cadena completa (incluye tu contraseña).
3. **Despliega en [Streamlit Community Cloud](https://share.streamlit.io)**
   (gratis, se conecta directo a tu cuenta de GitHub):
   - Nueva app → selecciona este repo → archivo principal `app/streamlit_app.py`.
   - En **Settings → Secrets**, agrega:
     ```toml
     DATABASE_URL = "postgresql://postgres:TU-PASSWORD@....supabase.co:5432/postgres"
     ```
   - Deploy. Streamlit te da una URL pública (algo como
     `tu-app.streamlit.app`) que abre en cualquier navegador, celular o PC,
     sin que nadie instale nada.
4. **Carga los datos reales** desde esa URL ya desplegada (Claves, Alcances,
   el Dataverse completo, y de ahí en adelante un CheLink por día).

> Nota importante: esta sesión de desarrollo (donde escribí el código) corre
> en un entorno con salida a internet restringida y no pudo conectarse
> directamente a Supabase para hacer la carga inicial por mí. Todo quedó
> validado end-to-end contra SQLite local con tus 5 archivos reales; la
> carga a producción la tienes que disparar tú (o yo desde otra sesión con
> salida a internet normal) usando los mismos botones de carga del
> dashboard, una vez desplegado.

## Reglas del motor (lo que decide qué es una alerta)

- **OOS**: la combinación tienda-SKU tiene historial de venta (promedio móvil
  de los últimos 90 días reportados > 0) y ese día el inventario es ≤ 0 y no
  hubo venta. Inventario negativo (existe en la data real de CheLink, por
  ajustes/tiempos de captura) cuenta igual que 0.
- **Phantom Inventory**: inventario > 0, sin venta, y la suma de "unidades
  esperadas y no vendidas" (promedio diario de cada día, sumado día a día)
  llega al umbral configurable (1.5 piezas por defecto). Antes de cruzar el
  umbral se marca como `en_observacion` (para que se vea venir, no solo
  cuando ya se disparó).
- **Racha y pérdida acumulada**: es un solo acumulador continuo — sigue
  sumando aunque el mismo combo pase de OOS a PI y viceversa — y solo se
  reinicia el día que vuelve a registrarse una venta real.
- **Huecos en el feed CheLink** (confirmado: hay días en que no llega una
  combinación tienda-SKU que sí llegó el día anterior o siguiente): un día
  sin dato **no** cuenta como día de incidencia ni resetea la racha; el
  último inventario conocido se conserva solo para no romper la gráfica de
  tendencia.
- **Ventas fuera de Alcances**: si una combinación tienda-SKU vende pero no
  está en el archivo de Alcances, igual se detecta y se reporta (Alcances no
  es un filtro, es solo referencia).
- **Pronóstico**: combina nivel base (promedio móvil de venta real) con
  tendencia (regresión lineal de los últimos 60 días), por combinación
  tienda-SKU. "Sobrevendiendo" / "bajo pronóstico" se marca cuando la venta
  real de los últimos 14 días se desvía más de 30% del pronóstico de esos
  mismos días.
- **Catálogo y Alcances versionados**: cada carga nueva de Claves.xlsx o
  Alcances.xlsx crea una versión con fecha de vigencia; no se pisa la
  anterior, así se pueden ampliar alcances o cambiar el catálogo sin perder
  la trazabilidad histórica.

## Calidad de datos detectada (para que no te sorprenda)

- **CheLink no manda todas las combinaciones todos los días** (confirmado
  comparando dos días reales: ~70-80 combinaciones tienda-SKU cambian de un
  día a otro). Tratado como hueco, no como incidencia (ver regla arriba).
- **Inventario negativo** aparece en la data real (varios miles de filas en
  la muestra probada). Tratado como agotado para efectos de OOS.
- **CheLink llega en latin-1**, no UTF-8, y con separador `|` y padding de
  espacios en las columnas — ya contemplado en el parser.
- **Dataverse trae columnas `Stockout_Flag`/`Venta_Cero_Flag` propias**, de
  un cálculo anterior que no se usó aquí (se recalculó todo desde cero con
  las reglas que confirmamos), para que las tres fuentes de datos (histórico,
  CheLink, y lo que se cargue a futuro) se evalúen siempre con la misma
  lógica.

## Configuración en vivo

Desde el propio dashboard (barra lateral, sin tocar código): umbral de
Phantom Inventory (piezas) y ventana de días que se recalculan en cada carga.
