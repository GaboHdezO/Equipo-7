"""Utilidades para exportar tablas filtradas a CSV/XLSX en memoria (para los
botones de descarga del dashboard)."""
from __future__ import annotations

import io

import pandas as pd


def a_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def a_xlsx_bytes(df: pd.DataFrame, hoja: str = "datos") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=hoja)
    return buf.getvalue()
