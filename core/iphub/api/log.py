# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The diary of the hub over HTTP: the last lines of what the daemon did, for the panel.

Reading it needs a session, because the lines carry addresses of equipment, identities,
licence ids and every command that crossed the installation. That is a map of the house.
"""

from aiohttp import web

from iphub.api.comum import LOG, com_sessao, resposta_ok
from iphub.log import LINHAS_MAXIMO


@com_sessao
async def listar(request: web.Request) -> web.Response:
    """Every line the diary still holds, oldest first, plus how many it dropped.

    Oldest first is the order a log is read in, and the panel appends to the bottom the
    way a terminal does; the count of what was dropped is what keeps a hole from reading as
    silence.
    """
    log = request.app[LOG]
    linhas = log.linhas()
    return resposta_ok(
        linhas=[linha.como_json() for linha in linhas],
        descartadas=log.descartadas,
        teto=LINHAS_MAXIMO,
    )
