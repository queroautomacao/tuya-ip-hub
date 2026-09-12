# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""What the owner checks about the box itself: the vault and the names it answers by."""

from aiohttp import web

from iphub.api.comum import AMBIENTE, NOMES_MDNS, com_sessao, resposta_ok
from iphub.cofre import abrir as abrir_cofre


@com_sessao
async def situacao(request: web.Request) -> web.Response:
    app = request.app
    amb = app[AMBIENTE]
    chaveiro = abrir_cofre(amb.dir_data)
    return resposta_ok(
        cofre={"presa_ao_hardware": chaveiro.presa_ao_hardware, "ilegiveis": chaveiro.ilegiveis},
        nomes=list(app[NOMES_MDNS]),
        porta=amb.porta,
    )
