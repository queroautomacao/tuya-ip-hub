# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Over HTTP: the schedules of the installation and the time zone they run in.

The list is saved whole, like the scenes: a schedule is a position in a list and the panel
edits the list. The time zone travels beside it because it is what makes seven o'clock mean
seven o'clock, and the answer says what time the hub thinks it is right now, so a wrong zone
is seen on the screen before the radio plays at four in the morning.
"""

import logging
from dataclasses import replace

from aiohttp import web

from iphub import agenda as modulo
from iphub.api.comum import (
    DESPERTADOR,
    com_sessao,
    config_de,
    ler_corpo,
    resposta_ok,
    trocar_config,
)
from iphub.api.licencas import CORPO_INVALIDO, ERRO_INTERNO, erro
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.agenda")

AGENDAMENTOS_INVALIDOS = "agendamentos_invalidos"
CORPO_MAXIMO_AGENDA = 64 * 1024


def _agendamento_json(agendamento: modulo.Agendamento) -> dict:
    return {
        "cena": agendamento.cena,
        "hora": agendamento.hora,
        "dias": list(agendamento.dias),
        "ativo": agendamento.ativo,
    }


def _situacao(request: web.Request) -> web.Response:
    cfg = config_de(request.app)
    momento = request.app[DESPERTADOR].momento()
    return resposta_ok(
        fuso=cfg.fuso,
        fuso_efetivo=str(momento.tzinfo),
        agora=momento.strftime("%H:%M"),
        agendamentos=[_agendamento_json(a) for a in cfg.agendamentos],
        maximo=modulo.MAXIMO,
    )


@com_sessao
async def listar(request: web.Request) -> web.Response:
    return _situacao(request)


@com_sessao
async def salvar(request: web.Request) -> web.Response:
    app = request.app
    dados = await ler_corpo(request, maximo=CORPO_MAXIMO_AGENDA)
    if dados is None:
        return erro(CORPO_INVALIDO)
    cfg = config_de(app)
    fuso = dados.get("fuso", cfg.fuso)
    if not modulo.fuso_valido(fuso):
        return resposta_erro(400, modulo.FUSO_INVALIDO)
    try:
        agendamentos = modulo.validar(dados.get("agendamentos", []))
    except modulo.AgendamentosInvalidos as recusa:
        return web.json_response(
            {
                "ok": False,
                "code": AGENDAMENTOS_INVALIDOS,
                "problemas": [{"campo": c, "codigo": k} for c, k in recusa.problemas],
            },
            status=400,
        )
    try:
        trocar_config(app, replace(cfg, fuso=fuso, agendamentos=agendamentos))
    except OSError as falha:
        log.error("could not write the schedules: %s", falha)
        return erro(ERRO_INTERNO)
    return _situacao(request)
