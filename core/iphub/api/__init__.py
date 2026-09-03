# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""REST API: one module per area, registered here.

API REST: um módulo por área, registrados aqui.
"""

from aiohttp import web

from iphub.api import declarativos, equipamentos, health, setup
from iphub.portao import TrataExpect, rota_delete, rota_get, rota_post


def registrar_rotas(app: web.Application, tratar_expect: TrataExpect) -> None:
    rota_get(app, "/health", health.health, tratar_expect)
    rota_get(app, "/api/estado", setup.estado, tratar_expect)
    rota_post(app, "/api/posse", setup.posse, tratar_expect)
    rota_post(app, "/api/entrar", setup.entrar, tratar_expect)
    rota_post(app, "/api/sair", setup.sair, tratar_expect)
    rota_get(app, "/api/sessao", setup.sessao, tratar_expect)
    rota_post(app, "/api/senha", setup.senha, tratar_expect)
    rota_get(app, "/api/catalogo", equipamentos.catalogo, tratar_expect)
    rota_get(app, "/api/equipamentos", equipamentos.listar, tratar_expect)
    rota_post(app, "/api/equipamentos", equipamentos.cadastrar, tratar_expect)
    rota_post(app, "/api/equipamentos/{identidade}", equipamentos.atualizar, tratar_expect)
    rota_delete(app, "/api/equipamentos/{identidade}", equipamentos.remover, tratar_expect)
    rota_post(app, "/api/equipamentos/{identidade}/acao", equipamentos.acao, tratar_expect)
    rota_post(
        app, "/api/equipamentos/{identidade}/autenticar", equipamentos.autenticar, tratar_expect
    )
    rota_post(app, "/api/descoberta", equipamentos.varredura, tratar_expect)
    # Why: the fixed paths come first, so a driver named "validar" or "modelo" could never
    # take the route of the validation or of the templates away from the panel.
    # Por que: os caminhos fixos vêm antes, para um driver chamado "validar" ou "modelo" nunca
    # tomar do painel a rota da validação ou a dos modelos.
    rota_post(app, "/api/drivers/validar", declarativos.validar, tratar_expect)
    rota_get(app, "/api/drivers/modelo/{transporte}", declarativos.modelo, tratar_expect)
    rota_get(app, "/api/drivers", declarativos.listar, tratar_expect)
    rota_post(app, "/api/drivers", declarativos.salvar, tratar_expect)
    rota_delete(app, "/api/drivers/{tipo}", declarativos.remover, tratar_expect)
