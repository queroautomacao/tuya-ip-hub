# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Wires the gate, the API and the panel into one aiohttp application."""

import asyncio
import functools
import time
from urllib.parse import urlsplit

from aiohttp import web

from iphub import anuncio, cofre
from iphub.agenda import Despertador
from iphub.ambiente import Ambiente
from iphub.api import registrar_rotas, sistema
from iphub.api.comum import (
    AMBIENTE,
    CATALOGO,
    CENAS,
    CONFIG,
    DESPERTADOR,
    GESTOR,
    LICENCAS,
    LIMITE,
    LOG,
    NOMES_MDNS,
    OTP_PENDENTE,
    REMOTO,
    SEGREDOS,
    SESSOES,
    TRAVA_POSSE,
    VARREDURA,
    VARREDURA_FAIXA,
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
from iphub.dpbus.socket import (
    BARRAMENTO,
    Barramento,
    Dormir,
    Relogio,
    baixar_barramento,
    subir_barramento,
)
from iphub.drivers.base import Driver
from iphub.drivers.catalogo import Catalogo
from iphub.drivers.gestor import Gestor
from iphub.limite import Limite
from iphub.log import instalar as instalar_log
from iphub.painel import registrar_painel
from iphub.portao import (
    criar_middleware_host,
    criar_middleware_origin,
    criar_middleware_remoto,
    criar_tratar_expect,
    gravar_cabecalhos,
    middleware_cabecalhos,
    middleware_erros_json,
    registrar_curinga,
)
from iphub.relay import Cliente as ClienteRelay
from iphub.remoto import Janela, Servico
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
    """The drivers of the image, or the ones a test names for the hub it is attacking."""
    if catalogo is None:
        return Catalogo(amb.dir_data)
    return Catalogo(amb.dir_data, nativos=dict(catalogo))


def relay_de(amb: Ambiente, cfg: Config) -> str:
    """The address of the relay: the environment of the appliance, or what config.json holds.

    The environment wins whenever it carries one, because it is the setting of the FLEET and
    nobody edits it at an installation; the value in config.json is what a hub kept from
    before, and it only answers for a box whose environment says nothing.
    """
    return amb.remoto_relay or cfg.remoto_relay.strip()


ANUNCIANTE = web.AppKey("anunciante", anuncio.Anunciante | None)


async def _subir_anuncio(app: web.Application) -> None:
    anunciante = app[ANUNCIANTE]
    if anunciante is not None:
        await anunciante.iniciar()


async def _baixar_anuncio(app: web.Application) -> None:
    anunciante = app[ANUNCIANTE]
    if anunciante is not None:
        anunciante.parar()


async def _subir_remoto(app: web.Application) -> None:
    """The window may already be open: a hub that rebooted inside one comes back into it."""
    await app[REMOTO].iniciar()


async def _baixar_remoto(app: web.Application) -> None:
    # The socket to the relay belongs to this daemon, and a daemon that ends leaves none.
    await app[REMOTO].parar()


async def _subir_gestor(app: web.Application) -> None:
    await app[GESTOR].iniciar()


async def _subir_despertador(app: web.Application) -> None:
    await app[DESPERTADOR].iniciar()


async def _baixar_despertador(app: web.Application) -> None:
    await app[DESPERTADOR].parar()


async def _baixar_gestor(app: web.Application) -> None:
    # A sweep in flight holds a socket and keeps answering the segment, so it is dropped
    # before the drivers are, and the loop closes with nothing of ours still running.
    tarefa = app[VARREDURA].valor
    if tarefa is not None:
        tarefa.cancel()
    faixa = app[VARREDURA_FAIXA].valor
    if faixa is not None:
        faixa[1].cancel()
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
    remoto: Servico | None = None,
    buscar_versao: sistema.BuscarVersao = sistema.buscar_ultima_versao_no_github,
) -> web.Application:
    # The bus is the only piece of the daemon that waits (five seconds for the first
    # frame, a second and a half for the reread), so a test moves those two by
    # hand instead of really sleeping them.
    # The Host check runs before every handler, so a rebinding probe gets 421 and nothing
    # else. The Expect handler repeats it because aiohttp runs Expect before the middlewares.
    # The routes persist the configuration, so the data directory has to exist before the
    # first request, not on the first write.
    garantir_diretorio(amb.dir_data)
    cfg = Mutavel(carregar_config(amb.dir_data) if config is None else config)
    segs = Mutavel(abrir_segredos(amb.dir_data) if segredos is None else segredos)

    def relay() -> str:
        return relay_de(amb, cfg.valor)

    def home():
        # What the hub tells the relay when it dials: the home of the app it belongs to,
        # written by the miniApp of the maker through the bus.
        return cfg.valor.remoto_home

    # The window and the socket to the relay come in as one piece, the way the clock does: a
    # test opens the door of a hub with no relay to dial and without waiting for a socket.
    servico = (
        Servico(Janela(amb.dir_data), ClienteRelay(relay, amb.porta, home))
        if remoto is None
        else remoto
    )
    janela = servico.janela

    def hosts_remotos() -> frozenset[str]:
        """The hostname of the relay counts as a host only while the window is open.

        Outside the window it is not merely unrouted: it is not in the allowlist, so a
        request that names it is answered 421 by the same check that closes DNS rebinding,
        before any route or any token is looked at.
        """
        if not janela.ativa():
            return frozenset()
        # The authority is the one of the relay, which is the same for a whole fleet; the
        # address of THIS window is a path under it.
        nome = (urlsplit(relay()).hostname or "").lower()
        return frozenset({nome}) if nome else frozenset()

    # The names the hub answers by on the LAN are hosts of its own, allowlist or not.
    nomes_mdns = anuncio.nomes_de(amb.nome, cofre.abrir(amb.dir_data).identidade)

    def obter_hosts() -> frozenset[str]:
        return frozenset(cfg.valor.hosts_permitidos) | frozenset(nomes_mdns) | hosts_remotos()

    app = web.Application(
        middlewares=[
            middleware_cabecalhos,
            criar_middleware_host(obter_hosts),
            criar_middleware_remoto(hosts_remotos),
            criar_middleware_origin(),
            middleware_erros_json,
        ]
    )
    app[NOMES_MDNS] = nomes_mdns
    app[ANUNCIANTE] = anuncio.Anunciante(nomes_mdns, amb.porta) if amb.mdns else None
    app.on_response_prepare.append(gravar_cabecalhos)
    app[INICIO] = time.monotonic()
    # The diary is installed before anything else is built, so what the catalog, the
    # gestor and the bus say while they rise is already in it when the panel first asks.
    app[LOG] = instalar_log()
    app[AMBIENTE] = amb
    app[CONFIG] = cfg
    app[SEGREDOS] = segs
    app[SESSOES] = Sessoes(amb.dir_data) if sessoes is None else sessoes
    app[LIMITE] = Limite() if limite is None else limite
    app[CATALOGO] = _catalogo_do_app(amb, catalogo)
    app[GESTOR] = Gestor(app[CATALOGO].drivers, cfg.valor.equipamentos)
    montar_dpbus(app, cfg.valor, dormir=dormir)
    # The bus owns no state of the installation; it takes the same door the
    # panel routes take (aplicar_dp and valores_dps), so a set that arrives over the socket
    # and a set that arrives over the licence routes land on the very same numbers and scenes.
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
    # The schedules read the configuration live, so a list saved by the panel counts on
    # the next tick with nothing to swap; the clock is the one the tests move by hand.
    app[DESPERTADOR] = Despertador(
        lambda: cfg.valor.agendamentos, lambda: cfg.valor.fuso, app[CENAS].executar, agora=agora
    )
    app[VARREDURA] = Mutavel(None)
    app[VARREDURA_FAIXA] = Mutavel(None)
    # What stops the process and what reaches the internet are the two things a test
    # must never do for real, so both come in as pieces the way the clock does.
    app[sistema.ENCERRAR] = encerrar
    app[sistema.BUSCAR_ULTIMA_VERSAO] = buscar_versao
    app[sistema.CACHE_ATUALIZACAO] = Mutavel(None)
    app[TRAVA_POSSE] = asyncio.Lock()
    app[REMOTO] = servico
    app[OTP_PENDENTE] = Mutavel("")
    app.on_startup.append(_subir_remoto)
    app.on_cleanup.append(_baixar_remoto)
    app.on_startup.append(_subir_anuncio)
    app.on_cleanup.append(_baixar_anuncio)
    app.on_startup.append(_subir_gestor)
    # After the gestor, because a schedule that fires on the first tick runs a scene, and a
    # scene runs on drivers that have to be mounted; before the bus for the same reason.
    app.on_startup.append(_subir_despertador)
    app.on_cleanup.append(_baixar_despertador)
    # On boot the bus takes down the zombie groups, which reaches the
    # speakers, so it rises AFTER the gestor mounted the drivers and falls BEFORE the gestor
    # drops them; a socket left open over drivers that are gone reads a hub that has no numbers.
    app.on_startup.append(subir_barramento)
    app.on_cleanup.append(baixar_barramento)
    app.on_cleanup.append(_baixar_gestor)
    tratar_expect = criar_tratar_expect(obter_hosts)
    registrar_rotas(app, tratar_expect)
    registrar_painel(app, amb.dir_painel, tratar_expect)
    # Registered last, so every real route is matched first and nothing falls through.
    registrar_curinga(app, tratar_expect)
    return app
