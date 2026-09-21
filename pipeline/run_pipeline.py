"""Orquestador: tras cualquier carga nueva, se recalculan alertas y pronóstico."""
from __future__ import annotations

from sqlalchemy.engine import Engine

from .forecast import calcular_pronostico
from .rules import calcular_alertas


def recalcular_todo(engine: Engine) -> dict:
    n_alertas = calcular_alertas(engine)
    n_pronostico = calcular_pronostico(engine)
    return {"alertas": n_alertas, "pronostico": n_pronostico}
