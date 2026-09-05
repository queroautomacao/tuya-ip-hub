# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The diary of the hub over HTTP: the last lines of what the daemon did, for the panel.

Why: reading it needs a session, because the lines carry addresses of equipment, identities,
licence ids and every command that crossed the installation. That is a map of the house.

O log do hub por HTTP: as últimas linhas do que o daemon fez, para o painel.

Por que: lê-lo exige sessão, porque as linhas levam endereços de equipamentos, identidades,
ids de licença e todo comando que atravessou a instalação. Isso é um mapa da casa.
"""

from aiohttp import web

from iphub.api.comum import LOG, com_sessao, resposta_ok
from iphub.log import LINHAS_MAXIMO


@com_sessao
async def listar(request: web.Request) -> web.Response:
    """Every line the diary still holds, oldest first, plus how many it dropped.

    Why: oldest first is the order a log is read in, and the panel appends to the bottom the
    way a terminal does; the count of what was dropped is what keeps a hole from reading as
    silence.

    Toda linha que o log ainda guarda, da mais velha para a mais nova, mais quantas ele
    descartou.

    Por que: da mais velha para a mais nova é a ordem em que um log é lido, e o painel
    acrescenta embaixo como um terminal faz; a conta do que foi descartado é o que impede um
    buraco de ser lido como silêncio.
    """
    log = request.app[LOG]
    linhas = log.linhas()
    return resposta_ok(
        linhas=[linha.como_json() for linha in linhas],
        descartadas=log.descartadas,
        teto=LINHAS_MAXIMO,
    )
