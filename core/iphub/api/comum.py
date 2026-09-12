# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Pieces shared by the API modules: typed app keys, body reading and the session guard."""

import asyncio
import functools
import json
import logging
import re
from dataclasses import fields, replace
from pathlib import Path

from aiohttp import web

from iphub import agenda as modulo_agenda
from iphub import cenas as modulo_cenas
from iphub.ambiente import Ambiente
from iphub.arquivos import ler_json
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Config
from iphub.config import carregar as carregar_config
from iphub.config import salvar as salvar_config
from iphub.dpbus import mapa, protocolo
from iphub.dpbus.numeros import Licencas
from iphub.drivers.base import Driver
from iphub.drivers.catalogo import Catalogo
from iphub.drivers.gestor import Gestor
from iphub.limite import Limite
from iphub.log import Log
from iphub.portao import CHAVE_REMOTO, Handler, resposta_erro
from iphub.remoto import Servico
from iphub.segredos import Segredos
from iphub.sessoes import Sessoes

CORPO_MAXIMO = 8 * 1024
CHAVE_TOKEN = "iphub_token"


class Mutavel[T]:
    """One slot a route may replace, because aiohttp freezes the app state on startup."""

    __slots__ = ("valor",)

    def __init__(self, valor: T) -> None:
        self.valor = valor


AMBIENTE = web.AppKey("ambiente", Ambiente)
CONFIG = web.AppKey("config", Mutavel[Config])
SEGREDOS = web.AppKey("segredos", Mutavel[Segredos])
SESSOES = web.AppKey("sessoes", Sessoes)
LIMITE = web.AppKey("limite", Limite)
CATALOGO = web.AppKey("catalogo", Catalogo)
GESTOR = web.AppKey("gestor", Gestor)
LOG = web.AppKey("log", Log)
VARREDURA = web.AppKey("varredura", Mutavel[asyncio.Task])
VARREDURA_FAIXA = web.AppKey("varredura_faixa", Mutavel[tuple[str, asyncio.Task] | None])
# With no ownership code, the ja_configurado check is the only guard of the claim
# route, and a check that is not atomic with the write hands two owners to two racers.
TRAVA_POSSE = web.AppKey("trava_posse", asyncio.Lock)
# The remote door: the service that owns the window and the socket to the relay, and the
# secret of an enrolment that is still on the screen of the owner.
REMOTO = web.AppKey("remoto", Servico)
OTP_PENDENTE = web.AppKey("otp_pendente", Mutavel[str])
# Has ONE book of licences and ONE list of scenes for the whole daemon, so
# the panel routes and the bus of the same hub command the same objects; two instances would
# form a group by one door and publish solo through the other.
log = logging.getLogger("iphub.api.comum")

LICENCAS = web.AppKey("licencas", Licencas)
CENAS = web.AppKey("cenas", modulo_cenas.Executor)
# The clock of the scenes: what runs one on its own at a time of day.
DESPERTADOR = web.AppKey("despertador", modulo_agenda.Despertador)
# The names the hub answers by on the LAN.
NOMES_MDNS = web.AppKey("nomes_mdns", tuple)


def montar_dpbus(
    app: web.Application, cfg: Config, *, dormir: modulo_cenas.Dormir = asyncio.sleep
) -> None:
    """The licences with their numbers and the scenes of the installation, as
    one wiring.
    """
    # The route validates an order and config.json does not, so the book of licences
    # judges every saved order on boot and leaves a number it refuses empty instead of
    # publishing a number nothing can command.
    app[LICENCAS] = Licencas(app[GESTOR], cfg.licencas, cfg.numeros)
    # A scene runs actions on equipment and the book of licences is what routes an
    # action through a group, so the executor is handed the same door the bus and the panel
    # use; there is no step that runs a scene, so a scene never starts another one.
    # The waits of a scene are the one thing the scenes do with a clock, so a test moves
    # them by hand the way it moves the waits of the bus.
    app[CENAS] = modulo_cenas.Executor(cfg.cenas, app[LICENCAS].acionar, dormir=dormir)
    # Registered before the cleanup of the gestor, so a scene in flight is taken off the
    # wire while the drivers it commands are still mounted.
    app.on_cleanup.append(_parar_cenas)


async def _parar_cenas(app: web.Application) -> None:
    await app[CENAS].parar()


def licencas_de(app: web.Application) -> Licencas:
    return app[LICENCAS]


def cenas_de(app: web.Application) -> modulo_cenas.Executor:
    return app[CENAS]


def produto_de_licenca(app: web.Application, licenca: object) -> str | None:
    return licencas_de(app).produto_de(licenca)


def valores_dps(app: web.Application, licenca: str) -> dict[int, object]:
    """Every reportable data point one licence holds right now."""
    livro = licencas_de(app)
    produto = livro.produto_de(licenca)
    if produto is None:
        return {}
    valores = livro.valores(licenca)
    servico = app[REMOTO]
    valores[mapa.DP_REMOTO] = servico.janela.ativa()
    # Minutes, rounded up, so a window with thirty seconds left still reads as one and not
    # as a door the app shows closed while it is open.
    valores[mapa.DP_REMOTO_RESTANTE] = -(-servico.janela.restante_s() // 60)
    valores[mapa.DP_REMOTO_CODIGO] = servico.janela.codigo()
    valores[mapa.DP_REMOTO_URL] = servico.url() if servico.janela.ativa() else ""
    valores[mapa.DP_REMOTO_HOME] = config_de(app).remoto_home
    # The two name data points carry the names of the scenes, which belong to the scenes
    # and not to the numbers; a list that does not fit the 255 bytes is left out instead of
    # published cut, because a cut JSON reaches the bridge impossible to read.
    try:
        primeira, segunda = mapa.nomes_das_cenas(cenas_de(app).nomes())
    except mapa.NomesInvalidos:
        return valores
    valores[mapa.dp_de(produto, "nomes_cenas", indice=1)] = primeira
    valores[mapa.dp_de(produto, "nomes_cenas", indice=2)] = segunda
    return valores


async def aplicar_dp(
    app: web.Application, licenca: object, dpid: object, valor: object
) -> str | None:
    """One set wherever it lands, done or refused with a stable code.

    The scene data point is the scene, which belongs to the scenes, and every other settable
    data point belongs to the numbers of the licence; the caller does not choose, so the
    panel route and the bus of the same hub cannot disagree about where a set goes.
    """
    livro = licencas_de(app)
    produto = livro.produto_de(licenca)
    if produto is None:
        return protocolo.LICENCA_DESCONHECIDA
    dp = mapa.de_dp(produto, dpid)
    if dp is None:
        return protocolo.DP_DESCONHECIDO
    if not dp.ajustavel:
        return protocolo.DP_SOMENTE_LEITURA
    if dp.funcao == "cena":
        numero = modulo_cenas.numero_de(valor)
        if numero is None:
            return protocolo.VALOR_INVALIDO
        return cenas_de(app).executar(numero)
    if dp.funcao == mapa.F_REMOTO:
        return await aplicar_remoto(app, valor)
    if dp.funcao == mapa.F_REMOTO_HOME:
        return aplicar_home(app, valor)
    return await livro.aplicar(licenca, dp.dpid, valor)


# The id of a home on the platform is a number; the same rule the relay applies before it
# asks the registry, so what this hub stores is what the relay would be willing to ask about.
_HOME = re.compile(r"[0-9]{1,20}")


def aplicar_home(app: web.Application, valor: object) -> str | None:
    """The home of the app, as the miniApp of the maker writes it: one number, kept."""
    if not isinstance(valor, str) or not _HOME.fullmatch(valor.strip()):
        return protocolo.VALOR_INVALIDO
    home = valor.strip()
    if home != config_de(app).remoto_home:
        trocar_config(app, replace(config_de(app), remoto_home=home))
        log.info("the remote access now belongs to a home of the app")
    return None


async def aplicar_remoto(app: web.Application, valor: object) -> str | None:
    """The remote door as the app of the customer presses it: one bool, twenty four hours.

    The same three conditions the panel checks are checked here, and for the same reason: a
    door that opens onto a hub with no second factor is a door that opens onto nothing. The
    refusal reaches the app as the invalid value of the bus, which is the only vocabulary the
    bridge speaks; the log line says which of the three was missing.
    """
    from iphub.api.remoto import faltando, fechar_janela

    if not isinstance(valor, bool):
        return protocolo.VALOR_INVALIDO
    servico = app[REMOTO]
    if not valor:
        await fechar_janela(app)
        return None
    falta = faltando(app)
    if falta:
        log.warning("the remote access was asked for from the app and %s", falta[0])
        return protocolo.VALOR_INVALIDO
    servico.janela.armar()
    await servico.reconciliar()
    log.info("the remote access was armed from the app")
    return None


def config_de(app: web.Application) -> Config:
    return app[CONFIG].valor


def relay_de(app: web.Application) -> str:
    """The address of the relay this hub dials: the environment first, config.json after."""
    return app[AMBIENTE].remoto_relay or config_de(app).remoto_relay.strip()


def segredos_de(app: web.Application) -> Segredos:
    return app[SEGREDOS].valor


def catalogo_de(app: web.Application) -> Catalogo:
    return app[CATALOGO]


def drivers_de(app: web.Application) -> dict[str, type[Driver]]:
    """The natives and the declarations that survived the loading, as one mapping."""
    return app[CATALOGO].drivers


def _config_do_disco(dir_data: Path, atual: Config) -> Config:
    """The file answers for the keys it carries, the live value for the keys it omits."""
    try:
        bruto = ler_json(dir_data / ARQUIVO_CONFIG)
        em_disco = carregar_config(dir_data)
    except (OSError, ValueError):
        # A file damaged after boot must not turn a password change into an error.
        return atual
    if bruto is None:
        return atual
    presentes = {atributo.name for atributo in fields(Config)} & set(bruto)
    return replace(atual, **{nome: getattr(em_disco, nome) for nome in presentes})


def trocar_config(app: web.Application, cfg: Config) -> None:
    """Persists and publishes the configuration, so no route writes the file by hand."""
    # The value in memory is the boot time snapshot, so writing it whole would erase a
    # hosts_permitidos the integrator edited by hand hours after the daemon came up.
    dir_data = app[AMBIENTE].dir_data
    atual = app[CONFIG].valor
    # The comparison walks the fields instead of asdict, because asdict would turn the
    # Cadastro of every equipment into a plain dict and put those dicts back into the Config.
    mudou = {
        campo.name: getattr(cfg, campo.name)
        for campo in fields(Config)
        if getattr(cfg, campo.name) != getattr(atual, campo.name)
    }
    mesclada = replace(_config_do_disco(dir_data, atual), **mudou)
    salvar_config(mesclada, dir_data)
    app[CONFIG].valor = mesclada


def resposta_ok(**campos: object) -> web.Response:
    return web.json_response({"ok": True, "code": None, **campos})


async def ler_corpo(request: web.Request, *, maximo: int = CORPO_MAXIMO) -> dict | None:
    """The body as a JSON object, or None for anything else, a body too large included."""
    # Whoever calls these routes is not authenticated yet, so the daemon reads a login
    # sized body and not one byte more. A route that carries a driver file declares the ceiling
    # of that file instead, because a login sized body would truncate an honest driver.
    if (request.content_length or 0) > maximo:
        return None
    bruto = await _corpo_inteiro(request, maximo)
    if bruto is None:
        return None
    try:
        dados = json.loads(bruto)
    # A body nested a few thousand levels deep raises RecursionError, which is not a
    # ValueError, so it left the route as a 500 with erro_interno and a traceback in the log
    # for a body that is simply invalid. The only honest outcome of a body this daemon cannot
    # read is corpo_invalido, whatever the parser raised.
    except Exception:
        return None
    return dados if isinstance(dados, dict) else None


async def _corpo_inteiro(request: web.Request, maximo: int) -> bytes | None:
    """Every byte of the body, or None when it goes past the ceiling."""
    # One read returns only what the buffer already holds, so a body that arrived in two
    # TCP segments came out truncated and an honest slow client could not log in.
    bruto = bytearray()
    while len(bruto) <= maximo:
        pedaco = await request.content.read(maximo + 1 - len(bruto))
        if not pedaco:
            return bytes(bruto)
        bruto += pedaco
    return None


def campo(dados: dict, chave: str) -> str | None:
    valor = dados.get(chave)
    return valor if isinstance(valor, str) else None


def token_do_cabecalho(bruto: str | None) -> str | None:
    """Bearer only; anything else is a header this daemon does not speak."""
    if not bruto:
        return None
    esquema, _, valor = bruto.partition(" ")
    if esquema.lower() != "bearer":
        return None
    return valor.strip() or None


def token_utilizavel(token: str) -> bool:
    """False for a token the header parser had to smuggle through surrogates."""
    # Header bytes that are not UTF-8 arrive as lone surrogates, and hashing one raises,
    # which answered 500 with a traceback where the honest answer is that no session matches.
    try:
        token.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def token_da_sessao(request: web.Request) -> str:
    return request[CHAVE_TOKEN]


def com_sessao(handler: Handler) -> Handler:
    """Decorator: the route runs only for a token the session store still accepts."""

    @functools.wraps(handler)
    async def guardado(request: web.Request) -> web.StreamResponse:
        token = token_do_cabecalho(request.headers.get("Authorization"))
        if token is None:
            return resposta_erro(401, "nao_autenticado")
        if not token_utilizavel(token) or not request.app[SESSOES].validar(token):
            return resposta_erro(401, "sessao_invalida")
        # A session opened on the LAN with the password alone is not a key to the door that
        # faces the internet; through the relay, only a session that showed both factors
        # passes, and the panel answers this code by asking for the code of the app.
        if request.get(CHAVE_REMOTO) and request.app[SESSOES].fatores_de(token) < 2:
            return resposta_erro(401, "otp_exigido")
        request[CHAVE_TOKEN] = token
        return await handler(request)

    return guardado
