# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Pieces shared by the API modules: typed app keys, body reading and the session guard.

Peças compartilhadas pelos módulos da API: chaves tipadas do app, leitura de corpo e a
guarda de sessão.
"""

import asyncio
import functools
import json
import logging
from dataclasses import fields, replace
from pathlib import Path

from aiohttp import web

from iphub import cenas as modulo_cenas
from iphub.ambiente import Ambiente
from iphub.arquivos import ler_json
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Config
from iphub.config import carregar as carregar_config
from iphub.config import salvar as salvar_config
from iphub.dpbus import mapa, protocolo
from iphub.dpbus.blocos import Blocos, OrdemInvalida
from iphub.drivers.base import Driver
from iphub.drivers.catalogo import Catalogo
from iphub.drivers.gestor import Gestor
from iphub.limite import Limite
from iphub.portao import Handler, resposta_erro
from iphub.segredos import Segredos
from iphub.sessoes import Sessoes

CORPO_MAXIMO = 8 * 1024
CHAVE_TOKEN = "iphub_token"


class Mutavel[T]:
    """One slot a route may replace, because aiohttp freezes the app state on startup.

    Um espaço que uma rota pode trocar, porque o aiohttp congela o estado do app ao subir.
    """

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
VARREDURA = web.AppKey("varredura", Mutavel[asyncio.Task])
# Why: with no ownership code, the ja_configurado check is the only guard of the claim
# route, and a check that is not atomic with the write hands two owners to two racers.
# Por que: sem código de posse, a checagem de ja_configurado é a única guarda da rota de
# posse, e uma checagem que não é atômica com a escrita entrega dois donos a dois
# concorrentes.
TRAVA_POSSE = web.AppKey("trava_posse", asyncio.Lock)
# Why: a save of a driver writes a file, re reads both directories and rebuilds what used the
# tipo; two of those crossing each other would reload a catalog over a half written folder.
# Por que: salvar um driver grava um arquivo, relê as duas pastas e refaz o que usava o tipo;
# dois desses se cruzando recarregariam um catálogo sobre uma pasta escrita pela metade.
TRAVA_DRIVERS = web.AppKey("trava_drivers", asyncio.Lock)
# Why: section 8 has ONE state of the blocks and ONE list of scenes for the whole daemon, so
# the panel routes and the bus of the same hub command the same objects; two instances would
# form a group by one door and publish solo through the other.
# Por que: a seção 8 tem UM estado de blocos e UMA lista de cenas para o daemon inteiro, então
# as rotas do painel e o barramento do mesmo hub comandam os mesmos objetos; duas instâncias
# formariam grupo por uma porta e publicariam solo pela outra.
log = logging.getLogger("iphub.api.comum")

BLOCOS = web.AppKey("blocos", Blocos)
CENAS = web.AppKey("cenas", modulo_cenas.Executor)


def _ordem_confiavel(gestor: Gestor, ordem: tuple[str, ...]) -> tuple[str, ...]:
    """The saved order with every block the blocks module refuses left empty.

    A ordem salva com todo bloco que o módulo dos blocos recusa deixado vazio.
    """
    juiz = Blocos(gestor)
    aceitos: list[str] = []
    for identidade in ordem[: mapa.BLOCOS]:
        try:
            juiz.validar([*aceitos, identidade])
        except OrdemInvalida as erro:
            log.warning("block %d of the saved order was dropped: %s", len(aceitos) + 1, erro)
            aceitos.append("")
        else:
            aceitos.append(identidade)
    return tuple(aceitos)


def montar_dpbus(app: web.Application, cfg: Config) -> None:
    """The six blocks of section 8 and the scenes of the installation, as one wiring.

    Os seis blocos da seção 8 e as cenas da instalação, numa ligação só.
    """
    # Why: the route validates the order and config.json does not, so an order edited by hand
    # boots a hub whose blocks name an identity that is not registered at all, or the same one
    # twice. The blocks module is the one that judges an order, so it judges this one too, and
    # a block it refuses is left empty instead of publishing a block nothing can command.
    # Por que: a rota valida a ordem e o config.json não, então uma ordem editada na mão sobe
    # um hub cujos blocos nomeiam uma identidade que nem está cadastrada, ou a mesma duas
    # vezes. O módulo dos blocos é quem julga uma ordem, então ele julga esta também, e um bloco
    # que ele recusa fica vazio em vez de publicar um bloco que ninguém comanda.
    app[BLOCOS] = Blocos(app[GESTOR], _ordem_confiavel(app[GESTOR], cfg.blocos))
    # Why: a scene sets data points and the blocks are what a data point reaches, so the
    # executor is handed the same door the bus and the panel use; a scene may not set DP 131
    # (the validation refuses it), so a scene never starts another one.
    # Por que: uma cena ajusta data points e os blocos são o que um data point alcança, então o
    # executor recebe a mesma porta que o barramento e o painel usam; uma cena não pode
    # ajustar o DP 131 (a validação recusa), então uma cena nunca dispara outra.
    app[CENAS] = modulo_cenas.Executor(cfg.cenas, app[BLOCOS].aplicar)
    # Why: registered before the cleanup of the gestor, so a scene in flight is taken off the
    # wire while the drivers it commands are still mounted.
    # Por que: registrado antes da limpeza do gestor, para uma cena em curso sair do fio
    # enquanto os drivers que ela comanda ainda estão montados.
    app.on_cleanup.append(_parar_cenas)


async def _parar_cenas(app: web.Application) -> None:
    await app[CENAS].parar()


def blocos_de(app: web.Application) -> Blocos:
    return app[BLOCOS]


def cenas_de(app: web.Application) -> modulo_cenas.Executor:
    return app[CENAS]


def valores_dps(app: web.Application) -> dict[int, object]:
    """Every reportable data point of section 8 this hub holds right now.

    Todo data point reportável da seção 8 que este hub tem agora.
    """
    valores = blocos_de(app).valores()
    # Why: DP 134 carries the names of the scenes, which belong to the scenes and not to the
    # blocks; a list that does not fit the 255 bytes is left out instead of published cut,
    # because a cut JSON reaches the bridge impossible to read.
    # Por que: o DP 134 leva os nomes das cenas, que são das cenas e não dos blocos; uma lista
    # que não cabe nos 255 bytes fica de fora em vez de sair cortada, porque um JSON cortado
    # chega à ponte impossível de ler.
    try:
        valores[mapa.NOMES_CENAS] = mapa.nomes_json(mapa.NOMES_CENAS, cenas_de(app).nomes())
    except mapa.NomesInvalidos:
        pass
    return valores


async def aplicar_dp(app: web.Application, dpid: object, valor: object) -> str | None:
    """One set of section 8 wherever it lands, done or refused with a stable code.

    DP 131 is the scene, which belongs to the scenes, and every other settable data point
    belongs to the blocks; the caller does not choose, so the panel route and the bus of the
    same hub cannot disagree about where a set goes.

    Um set da seção 8 onde quer que ele caia, feito ou recusado com um código estável.

    O DP 131 é a cena, que é das cenas, e todo outro data point ajustável é dos blocos; quem
    chama não escolhe, então a rota do painel e o barramento do mesmo hub não podem discordar
    sobre para onde vai um set.
    """
    dp = mapa.de_dp(dpid)
    if dp is None:
        return protocolo.DP_DESCONHECIDO
    if not dp.ajustavel:
        return protocolo.DP_SOMENTE_LEITURA
    if dp.dpid == mapa.CENA:
        numero = modulo_cenas.numero_de(valor)
        if numero is None:
            return protocolo.VALOR_INVALIDO
        return cenas_de(app).executar(numero)
    return await blocos_de(app).aplicar(dp.dpid, valor)


def config_de(app: web.Application) -> Config:
    return app[CONFIG].valor


def segredos_de(app: web.Application) -> Segredos:
    return app[SEGREDOS].valor


def catalogo_de(app: web.Application) -> Catalogo:
    return app[CATALOGO]


def drivers_de(app: web.Application) -> dict[str, type[Driver]]:
    """The natives and the declarations that survived the loading, as one mapping.

    Os nativos e as declarações que sobreviveram à carga, como um mapa só.
    """
    return app[CATALOGO].drivers


def _config_do_disco(dir_data: Path, atual: Config) -> Config:
    """The file answers for the keys it carries, the live value for the keys it omits.

    O arquivo responde pelas chaves que carrega, o valor vivo pelas que ele omite.
    """
    try:
        bruto = ler_json(dir_data / ARQUIVO_CONFIG)
        em_disco = carregar_config(dir_data)
    except (OSError, ValueError):
        # Why: a file damaged after boot must not turn a password change into an error.
        # Por que: um arquivo danificado depois do boot não pode virar erro na troca de senha.
        return atual
    if bruto is None:
        return atual
    presentes = {atributo.name for atributo in fields(Config)} & set(bruto)
    return replace(atual, **{nome: getattr(em_disco, nome) for nome in presentes})


def trocar_config(app: web.Application, cfg: Config) -> None:
    """Persists and publishes the configuration, so no route writes the file by hand.

    Grava e publica a configuração, para nenhuma rota escrever o arquivo na mão.
    """
    # Why: the value in memory is the boot time snapshot, so writing it whole would erase a
    # hosts_permitidos the integrator edited by hand hours after the daemon came up.
    # Por que: o valor em memória é o retrato do boot, então gravá-lo inteiro apagaria um
    # hosts_permitidos que o integrador editou na mão horas depois de o daemon subir.
    dir_data = app[AMBIENTE].dir_data
    atual = app[CONFIG].valor
    # Why: the comparison walks the fields instead of asdict, because asdict would turn the
    # Cadastro of every equipment into a plain dict and put those dicts back into the Config.
    # Por que: a comparação percorre os campos em vez do asdict, porque o asdict tornaria o
    # Cadastro de cada equipamento um dict cru e devolveria esses dicts para dentro do Config.
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
    """The body as a JSON object, or None for anything else, a body too large included.

    O corpo como objeto JSON, ou None para qualquer outra coisa, inclusive corpo grande demais.
    """
    # Why: whoever calls these routes is not authenticated yet, so the daemon reads a login
    # sized body and not one byte more. A route that carries a driver file declares the ceiling
    # of that file instead, because a login sized body would truncate an honest driver.
    # Por que: quem chama estas rotas ainda não está autenticado, então o daemon lê um corpo
    # do tamanho de um login e nem um byte a mais. Uma rota que leva um arquivo de driver
    # declara o teto daquele arquivo, porque um corpo de login truncaria um driver honesto.
    if (request.content_length or 0) > maximo:
        return None
    bruto = await _corpo_inteiro(request, maximo)
    if bruto is None:
        return None
    try:
        dados = json.loads(bruto)
    # Why: a body nested a few thousand levels deep raises RecursionError, which is not a
    # ValueError, so it left the route as a 500 with erro_interno and a traceback in the log
    # for a body that is simply invalid. The only honest outcome of a body this daemon cannot
    # read is corpo_invalido, whatever the parser raised.
    # Por que: um corpo aninhado alguns milhares de níveis estoura RecursionError, que não é
    # ValueError, então ele saía da rota como 500 com erro_interno e traceback no log por um
    # corpo que é simplesmente inválido. O único desfecho honesto de um corpo que este daemon
    # não consegue ler é corpo_invalido, qualquer que tenha sido o que o parser levantou.
    except Exception:
        return None
    return dados if isinstance(dados, dict) else None


async def _corpo_inteiro(request: web.Request, maximo: int) -> bytes | None:
    """Every byte of the body, or None when it goes past the ceiling.

    Todo byte do corpo, ou None quando ele passa do teto.
    """
    # Why: one read returns only what the buffer already holds, so a body that arrived in two
    # TCP segments came out truncated and an honest slow client could not log in.
    # Por que: uma leitura só devolve o que o buffer já tem, então um corpo que chegou em dois
    # segmentos TCP saía truncado e um cliente honesto e lento não conseguia entrar.
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
    """Bearer only, section 8; anything else is a header this daemon does not speak.

    Só Bearer, seção 8; qualquer outra coisa é um cabeçalho que este daemon não fala.
    """
    if not bruto:
        return None
    esquema, _, valor = bruto.partition(" ")
    if esquema.lower() != "bearer":
        return None
    return valor.strip() or None


def token_utilizavel(token: str) -> bool:
    """False for a token the header parser had to smuggle through surrogates.

    Falso para um token que o analisador de cabeçalho teve de contrabandear em surrogates.
    """
    # Why: header bytes that are not UTF-8 arrive as lone surrogates, and hashing one raises,
    # which answered 500 with a traceback where the honest answer is that no session matches.
    # Por que: bytes de cabeçalho fora do UTF-8 chegam como surrogates soltos, e passar um por
    # hash estoura, o que respondia 500 com traceback onde a resposta honesta é que nenhuma
    # sessão casa.
    try:
        token.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def token_da_sessao(request: web.Request) -> str:
    return request[CHAVE_TOKEN]


def com_sessao(handler: Handler) -> Handler:
    """Decorator: the route runs only for a token the session store still accepts.

    Decorador: a rota só roda para um token que o repositório de sessões ainda aceita.
    """

    @functools.wraps(handler)
    async def guardado(request: web.Request) -> web.StreamResponse:
        token = token_do_cabecalho(request.headers.get("Authorization"))
        if token is None:
            return resposta_erro(401, "nao_autenticado")
        if not token_utilizavel(token) or not request.app[SESSOES].validar(token):
            return resposta_erro(401, "sessao_invalida")
        request[CHAVE_TOKEN] = token
        return await handler(request)

    return guardado
