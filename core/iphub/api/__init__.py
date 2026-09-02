# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""REST API: one module per area, registered here.

API REST: um módulo por área, registrados aqui.
"""

from aiohttp import web

from iphub.api import health
from iphub.portao import TrataExpect, rota_get


def registrar_rotas(app: web.Application, tratar_expect: TrataExpect) -> None:
    rota_get(app, "/health", health.health, tratar_expect)
