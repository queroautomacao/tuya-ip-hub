# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Wires the gate, the API and the panel into one aiohttp application.

Liga o portão, a API e o painel numa única aplicação aiohttp.
"""

import asyncio
import functools
import time

from aiohttp import web

from iphub.ambiente import Ambiente
from iphub.api import registrar_rotas, sistema
from iphub.api.comum import (
    AMBIENTE,
    CATALOGO,
    CONFIG,
    DIARIO,
    GESTOR,
    LICENCAS,
    LIMITE,
    SEGREDOS,
    SESSOES,
    TRAVA_DRIVERS,
    TRAVA_POSSE,
    VARREDURA,
    Mutavel,
    aplicar_dp,
    montar_dpbus,
    trocar_config,
    valores_dps,
)
from iphub.api.health import INICIO
from iphub.arquivos import garantir_diretorio
from iphub.config import Config
from iphub.config import carregar as carregar_config
from iphub.diario import instalar as instalar_diario
from iphub.dpbus.socket import (
    BARRAMENTO,
    Barramento,
    Dormir,
    Relogio,
    baixar_barramento,
    subir_barramento,
)
from iphub.drivers import catalogo as modulo_catalogo
from iphub.drivers.base import Driver
from iphub.drivers.catalogo import Catalogo
from iphub.drivers.gestor import Gestor
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

__all__ = [
    "AMBIENTE",
    "CATALOGO",
    "CONFIG",
    "GESTOR",
    "LIMITE",
    "SEGREDOS",
    "SESSOES",
    "criar_app",
    "trocar_config",
]


def _catalogo_do_app(amb: Ambiente, catalogo: dict[str, type[Driver]] | None) -> Catalogo:
    """The drivers of the image plus the JSON of the data directory, section 7.

    Os drivers da imagem mais o JSON do diretório de dados, seção 7.
    """
    # Why: a test names the drivers of the hub it is attacking, and the examples that ship in
    # the image would answer its listing with drivers it never registered; the JSON of the data
    # directory still loads, because that is the directory the test itself writes into.
    # Por que: um teste nomeia os drivers do hub que ele ataca, e os exemplos que embarcam na
    # imagem responderiam à listagem dele com drivers que ele nunca cadastrou; o JSON do
    # diretório de dados segue carregando, porque é nele que o próprio teste grava.
    if catalogo is None:
        return Catalogo(amb.dir_data, pasta_embarcada=modulo_catalogo.PASTA_EMBARCADA)
    return Catalogo(amb.dir_data, nativos=dict(catalogo), pasta_embarcada=None)


async def _subir_gestor(app: web.Application) -> None:
    await app[GESTOR].iniciar()


async def _baixar_gestor(app: web.Application) -> None:
    # Why: a sweep in flight holds a socket and keeps answering the segment, so it is dropped
    # before the drivers are, and the loop closes with nothing of ours still running.
    # Por que: uma varredura em curso segura um socket e segue respondendo ao segmento, então
    # ela cai antes dos drivers, e o laço fecha sem nada nosso rodando.
    tarefa = app[VARREDURA].valor
    if tarefa is not None:
        tarefa.cancel()
    await app[GESTOR].parar()


def criar_app(
    amb: Ambiente,
    *,
    config: Config | None = None,
    sessoes: Sessoes | None = None,
    limite: Limite | None = None,
    segredos: Segredos | None = None,
    catalogo: dict[str, type[Driver]] | None = None,
    dormir: Dormir = asyncio.sleep,
    agora: Relogio = time.time,
    encerrar: sistema.Encerrar = sistema.encerrar_processo,
    buscar_versao: sistema.BuscarVersao = sistema.buscar_ultima_versao_no_github,
) -> web.Application:
    # Why: the bus is the only piece of the daemon that waits (five seconds for the first
    # frame, a second and a half for the reread of section 8), so a test moves those two by
    # hand instead of really sleeping them.
    # Por que: o barramento é a única peça do daemon que espera (cinco segundos pelo primeiro
    # quadro, um segundo e meio pela releitura da seção 8), então um teste move essas duas na
    # mão em vez de dormi-las de verdade.
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
    # Why: the diary is installed before anything else is built, so what the catalog, the
    # gestor and the bus say while they rise is already in it when the panel first asks.
    # Por que: o diário é instalado antes de tudo o mais ser construído, então o que o
    # catálogo, o gestor e o barramento dizem ao subir já está nele quando o painel pergunta.
    app[DIARIO] = instalar_diario()
    app[AMBIENTE] = amb
    app[CONFIG] = cfg
    app[SEGREDOS] = segs
    app[SESSOES] = Sessoes(amb.dir_data) if sessoes is None else sessoes
    app[LIMITE] = Limite() if limite is None else limite
    app[CATALOGO] = _catalogo_do_app(amb, catalogo)
    app[GESTOR] = Gestor(app[CATALOGO].drivers, cfg.valor.equipamentos)
    montar_dpbus(app, cfg.valor, dormir=dormir)
    # Why: the bus of section 8 owns no state of the installation; it takes the same door the
    # panel routes take (aplicar_dp and valores_dps), so a set that arrives over the socket
    # and a set that arrives over the licence routes land on the very same numbers and scenes.
    # Por que: o barramento da seção 8 não é dono de estado da instalação; ele toma a mesma
    # porta que as rotas do painel tomam (aplicar_dp e valores_dps), então um set que chega
    # pelo socket e um que chega pelas rotas de licença caem nos mesmíssimos números e cenas.
    livro = app[LICENCAS]
    app[BARRAMENTO] = Barramento(
        functools.partial(aplicar_dp, app),
        functools.partial(valores_dps, app),
        lambda: segs.valor.api_token,
        livro.produto_de,
        licencas=livro.ids,
        sanear=livro.sanear,
        sincronizar=livro.sincronizar,
        reler=livro.reler,
        dormir=dormir,
        agora=agora,
    )
    app[VARREDURA] = Mutavel(None)
    # Why: what stops the process and what reaches the internet are the two things a test
    # must never do for real, so both come in as pieces the way the clock does.
    # Por que: o que para o processo e o que alcança a internet são as duas coisas que um
    # teste nunca pode fazer de verdade, então as duas entram como peças, como o relógio.
    app[sistema.ENCERRAR] = encerrar
    app[sistema.BUSCAR_ULTIMA_VERSAO] = buscar_versao
    app[sistema.CACHE_ATUALIZACAO] = Mutavel(None)
    app[TRAVA_POSSE] = asyncio.Lock()
    app[TRAVA_DRIVERS] = asyncio.Lock()
    app.on_startup.append(_subir_gestor)
    # Why: on boot the bus takes down the zombie groups of section 14, which reaches the
    # speakers, so it rises AFTER the gestor mounted the drivers and falls BEFORE the gestor
    # drops them; a socket left open over drivers that are gone reads a hub that has no numbers.
    # Por que: no boot o barramento derruba o grupo zumbi da seção 14, o que alcança as caixas,
    # então ele sobe DEPOIS de o gestor montar os drivers e cai ANTES de o gestor os largar; um
    # socket aberto sobre drivers que já foram lê um hub sem número nenhum.
    app.on_startup.append(subir_barramento)
    app.on_cleanup.append(baixar_barramento)
    app.on_cleanup.append(_baixar_gestor)
    tratar_expect = criar_tratar_expect(obter_hosts)
    registrar_rotas(app, tratar_expect)
    registrar_painel(app, amb.dir_painel, tratar_expect)
    # Why: registered last, so every real route is matched first and nothing falls through.
    # Por que: registrado por último, para toda rota real casar antes e nada escapar.
    registrar_curinga(app, tratar_expect)
    return app
