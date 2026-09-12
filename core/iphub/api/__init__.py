# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""REST API: one module per area, registered here."""

from aiohttp import web

from iphub.api import (
    agenda,
    backup,
    cenas,
    equipamentos,
    health,
    licencas,
    log,
    remoto,
    seguranca,
    setup,
    sistema,
)
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
    rota_post(app, "/api/instalacao", setup.instalacao, tratar_expect)
    # The installation as one file, out with a session and in with the password of the file;
    # restoring is what a new box does, so a hub with no owner answers it too.
    rota_post(app, "/api/backup", backup.exportar, tratar_expect)
    rota_post(app, "/api/restaurar", backup.restaurar, tratar_expect)
    # The remote door: the window and the second factor.
    rota_get(app, "/api/remoto", remoto.situacao, tratar_expect)
    rota_post(app, "/api/remoto", remoto.armar, tratar_expect)
    rota_post(app, "/api/otp", remoto.otp_comecar, tratar_expect)
    rota_post(app, "/api/otp/confirmar", remoto.otp_confirmar, tratar_expect)
    rota_delete(app, "/api/otp", remoto.otp_remover, tratar_expect)
    rota_post(app, "/api/reiniciar", sistema.reiniciar, tratar_expect)
    rota_get(app, "/api/seguranca", seguranca.situacao, tratar_expect)
    rota_get(app, "/api/atualizacao", sistema.atualizacao, tratar_expect)
    rota_get(app, "/api/log", log.listar, tratar_expect)
    rota_get(app, "/api/rede", equipamentos.rede, tratar_expect)
    rota_get(app, "/api/catalogo", equipamentos.catalogo, tratar_expect)
    rota_get(app, "/api/equipamentos", equipamentos.listar, tratar_expect)
    rota_post(app, "/api/equipamentos", equipamentos.cadastrar, tratar_expect)
    rota_post(app, "/api/equipamentos/{identidade}", equipamentos.atualizar, tratar_expect)
    rota_delete(app, "/api/equipamentos/{identidade}", equipamentos.remover, tratar_expect)
    rota_post(app, "/api/equipamentos/{identidade}/acao", equipamentos.acao, tratar_expect)
    rota_post(
        app, "/api/equipamentos/{identidade}/autenticar", equipamentos.autenticar, tratar_expect
    )
    rota_post(app, "/api/catalogo/{tipo}/aparelhos", equipamentos.aparelhos_da_conta, tratar_expect)
    rota_post(app, "/api/descoberta", equipamentos.varredura, tratar_expect)
    # The fixed paths come first, so a driver named "validar" or "modelo" could never
    # take the route of the validation or of the templates away from the panel.
    rota_get(app, "/api/licencas", licencas.listar, tratar_expect)
    rota_post(app, "/api/licencas", licencas.criar, tratar_expect)
    rota_post(app, "/api/licencas/{id}", licencas.atualizar, tratar_expect)
    rota_delete(app, "/api/licencas/{id}", licencas.remover, tratar_expect)
    rota_post(app, "/api/licencas/{id}/numeros", licencas.definir_numeros, tratar_expect)
    rota_get(app, "/api/licencas/{id}/dps", licencas.dps, tratar_expect)
    rota_post(app, "/api/licencas/{id}/dp/{dpid}", licencas.ajustar, tratar_expect)
    rota_post(app, "/api/licencas/{id}/grupo", licencas.grupo, tratar_expect)
    rota_get(app, "/api/licencas/{id}/qr", licencas.qr, tratar_expect)
    # The fixed path comes first, so a scene number could never take the route of the
    # list away from the panel.
    rota_get(app, "/api/cenas", cenas.listar, tratar_expect)
    rota_post(app, "/api/cenas", cenas.salvar, tratar_expect)
    rota_post(app, "/api/cenas/{numero}/executar", cenas.executar, tratar_expect)
    rota_get(app, "/api/agendamentos", agenda.listar, tratar_expect)
    rota_post(app, "/api/agendamentos", agenda.salvar, tratar_expect)
    # The bus of the bridge is not part of /api/ and carries no session; it
    # authenticates on its FIRST frame with the api_token, which never travels in the URL.
    rota_get(app, "/dpbus", socket.dpbus, tratar_expect)
