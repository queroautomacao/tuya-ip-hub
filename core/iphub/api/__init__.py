# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""REST API: one module per area, registered here.

API REST: um módulo por área, registrados aqui.
"""

from aiohttp import web

from iphub.api import cenas, declarativos, equipamentos, health, setup
from iphub.api import zonas as rotas_zonas
from iphub.dpbus import socket
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
    rota_get(app, "/api/zonas", rotas_zonas.listar, tratar_expect)
    rota_post(app, "/api/zonas", rotas_zonas.definir, tratar_expect)
    rota_get(app, "/api/dps", rotas_zonas.dps, tratar_expect)
    rota_post(app, "/api/dp/{dpid}", rotas_zonas.ajustar, tratar_expect)
    rota_post(app, "/api/grupo", rotas_zonas.grupo, tratar_expect)
    # Why: the fixed path comes first, so a scene number could never take the route of the
    # list away from the panel.
    # Por que: o caminho fixo vem antes, para um número de cena nunca tomar do painel a rota
    # da listagem.
    rota_get(app, "/api/cenas", cenas.listar, tratar_expect)
    rota_post(app, "/api/cenas", cenas.salvar, tratar_expect)
    rota_post(app, "/api/cenas/{numero}/executar", cenas.executar, tratar_expect)
    # Why: section 8, the bus of the bridge is not part of /api/ and carries no session; it
    # authenticates on its FIRST frame with the api_token, which never travels in the URL.
    # Por que: seção 8, o barramento da ponte não faz parte do /api/ e não leva sessão; ele
    # autentica no PRIMEIRO quadro com o api_token, que nunca viaja na URL.
    rota_get(app, "/dpbus", socket.dpbus, tratar_expect)
