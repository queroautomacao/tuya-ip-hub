# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Wires the gate, the API and the panel into one aiohttp application.

Liga o portão, a API e o painel numa única aplicação aiohttp.
"""

import time

from aiohttp import web

from iphub.ambiente import Ambiente
from iphub.api import registrar_rotas
from iphub.api.comum import AMBIENTE, CONFIG, LIMITE, SEGREDOS, SESSOES, Mutavel, trocar_config
from iphub.api.health import INICIO
from iphub.arquivos import garantir_diretorio
from iphub.config import Config
from iphub.config import carregar as carregar_config
from iphub.limite import Limite
from iphub.painel import registrar_painel
from iphub.portao import (
    criar_middleware_host,
    criar_middleware_origin,
    criar_tratar_expect,
    gravar_cabecalhos,
    middleware_cabecalhos,
    middleware_erros_json,
    registrar_curinga,
)
from iphub.segredos import Segredos
from iphub.segredos import abrir as abrir_segredos
from iphub.sessoes import Sessoes

__all__ = ["AMBIENTE", "CONFIG", "LIMITE", "SEGREDOS", "SESSOES", "criar_app", "trocar_config"]


def criar_app(
    amb: Ambiente,
    *,
    config: Config | None = None,
    sessoes: Sessoes | None = None,
    limite: Limite | None = None,
    segredos: Segredos | None = None,
) -> web.Application:
    # Why: the Host check runs before every handler, so a rebinding probe gets 421 and nothing
    # else. The Expect handler repeats it because aiohttp runs Expect before the middlewares.
    # Por que: o Host é checado antes de todo handler, então uma sonda de rebinding recebe 421
    # e nada mais. O tratador de Expect repete a checagem porque o aiohttp roda Expect antes
    # dos middlewares.
    # Why: the routes persist the configuration, so the data directory has to exist before the
    # first request, not on the first write.
    # Por que: as rotas gravam a configuração, então o diretório de dados precisa existir antes
    # da primeira requisição, não na primeira escrita.
    garantir_diretorio(amb.dir_data)
    cfg = Mutavel(carregar_config(amb.dir_data) if config is None else config)
    segs = Mutavel(abrir_segredos(amb.dir_data) if segredos is None else segredos)

    def obter_hosts() -> frozenset[str]:
        return frozenset(cfg.valor.hosts_permitidos)

    app = web.Application(
        middlewares=[
            middleware_cabecalhos,
            criar_middleware_host(obter_hosts),
            criar_middleware_origin(),
            middleware_erros_json,
        ]
    )
    app.on_response_prepare.append(gravar_cabecalhos)
    app[INICIO] = time.monotonic()
    app[AMBIENTE] = amb
    app[CONFIG] = cfg
    app[SEGREDOS] = segs
    app[SESSOES] = Sessoes(amb.dir_data) if sessoes is None else sessoes
    app[LIMITE] = Limite() if limite is None else limite
    tratar_expect = criar_tratar_expect(obter_hosts)
    registrar_rotas(app, tratar_expect)
    registrar_painel(app, amb.dir_painel, tratar_expect)
    # Why: registered last, so every real route is matched first and nothing falls through.
    # Por que: registrado por último, para toda rota real casar antes e nada escapar.
    registrar_curinga(app, tratar_expect)
    return app
