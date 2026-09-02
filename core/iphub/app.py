# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Wires the gate, the API and the panel into one aiohttp application.

Liga o portão, a API e o painel numa única aplicação aiohttp.
"""

import time

from aiohttp import web

from iphub.ambiente import Ambiente
from iphub.api import registrar_rotas
from iphub.api.health import INICIO
from iphub.painel import registrar_painel
from iphub.portao import (
    criar_middleware_host,
    criar_tratar_expect,
    gravar_cabecalhos,
    middleware_cabecalhos,
    middleware_erros_json,
    registrar_curinga,
)


def criar_app(amb: Ambiente, hosts_permitidos: frozenset[str] = frozenset()) -> web.Application:
    # Why: the Host check runs before every handler, so a rebinding probe gets 421 and nothing
    # else. The Expect handler repeats it because aiohttp runs Expect before the middlewares.
    # Por que: o Host é checado antes de todo handler, então uma sonda de rebinding recebe 421
    # e nada mais. O tratador de Expect repete a checagem porque o aiohttp roda Expect antes
    # dos middlewares.
    app = web.Application(
        middlewares=[
            middleware_cabecalhos,
            criar_middleware_host(hosts_permitidos),
            middleware_erros_json,
        ]
    )
    app.on_response_prepare.append(gravar_cabecalhos)
    app[INICIO] = time.monotonic()
    tratar_expect = criar_tratar_expect(hosts_permitidos)
    registrar_rotas(app, tratar_expect)
    registrar_painel(app, amb.dir_painel, tratar_expect)
    # Why: registered last, so every real route is matched first and nothing falls through.
    # Por que: registrado por último, para toda rota real casar antes e nada escapar.
    registrar_curinga(app, tratar_expect)
    return app
