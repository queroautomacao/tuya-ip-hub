# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""REST API: one module per area, registered here.

API REST: um módulo por área, registrados aqui.
"""

from aiohttp import web

from iphub.api import health, setup
from iphub.portao import TrataExpect, rota_get, rota_post


def registrar_rotas(app: web.Application, tratar_expect: TrataExpect) -> None:
    rota_get(app, "/health", health.health, tratar_expect)
    rota_get(app, "/api/estado", setup.estado, tratar_expect)
    rota_post(app, "/api/posse", setup.posse, tratar_expect)
    rota_post(app, "/api/entrar", setup.entrar, tratar_expect)
    rota_post(app, "/api/sair", setup.sair, tratar_expect)
    rota_get(app, "/api/sessao", setup.sessao, tratar_expect)
    rota_post(app, "/api/senha", setup.senha, tratar_expect)
