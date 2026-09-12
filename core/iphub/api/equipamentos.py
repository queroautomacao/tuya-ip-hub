# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Equipment routes: the catalog, the registrations, one action and the discovery sweep.

The gestor imposes the rules and answers a stable code; these routes only
turn that code into a status and never invent one of their own. The address a route takes
is an IP literal, so the hub never reaches a host nobody registered.
"""

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import replace
from math import isfinite

from aiohttp import web

from iphub import rede as rede_local
from iphub.api.comum import (
    CATALOGO,
    GESTOR,
    VARREDURA,
    VARREDURA_FAIXA,
    com_sessao,
    config_de,
    drivers_de,
    ler_corpo,
    licencas_de,
    resposta_ok,
    trocar_config,
)
from iphub.api.formato import achado_json, equipamento_json, manifesto_json
from iphub.config import (
    LISTAS,
    LISTAS_MAXIMO,
    NIVEL_MAXIMO,
    Cadastro,
    Item,
    ip_literal,
    item_valido,
)
from iphub.dpbus import mapa, perfil, protocolo
from iphub.dpbus import numeros as modulo_numeros
from iphub.drivers import descoberta, presenca
from iphub.drivers import varredura as modulo_varredura
from iphub.drivers.base import PAREADO
from iphub.drivers.gestor import ErroDeCadastro, Gestor
from iphub.drivers.manifesto import Manifesto, TipoCampo, produto_de
from iphub.drivers.manifesto import por_lista as sugestoes_por_lista
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.equipamentos")

# The sweep waits for the segment to answer, and the hard limit is what keeps a socket
# that never returns from holding the route open until the panel gives up.
TIMEOUT_VARREDURA_S = 3.0
LIMITE_VARREDURA_S = 8.0

# A range of 254 addresses asked by nine drivers is a lot of sockets, so the ceiling of
# the whole sweep is what keeps the panel from waiting on a segment that swallows packets
# instead of refusing them. Two seconds per probe is generous for a device on the same
# segment and short enough that an address with nothing on it costs almost nothing.
LIMITE_FAIXA_S = 45.0

# The account of a maker answers or it does not, and the operator is at the keyboard.
LIMITE_DA_CONTA_S = 20.0
DESTINO = descoberta.DESTINO_PADRAO
DESTINO_MDNS = descoberta.DESTINO_MDNS

TEXTO_MAXIMO = 200

# The actions that a group routes to the master, which the book of licences
# does; everything else goes straight to the equipment the way it always did.
ACOES_ROTEADAS = ("volume", *modulo_numeros.DO_MESTRE)

CORPO_INVALIDO = "corpo_invalido"
NAO_SUPORTADO = "nao_suportado"
# 409 already, because a credential that is not accepted yet is a conflict of state and
# not a malformed request; the code and the status were fixed before this route existed.
AUTH_PENDENTE = "auth_pendente"
CAMPO_INVALIDO = "campo_invalido"
TIPO_DESCONHECIDO = "tipo_desconhecido"
IP_INVALIDO = "ip_invalido"
INVALID_VALUE = "invalid_value"
ERRO_INTERNO = "erro_interno"
EQ_NAO_ENCONTRADO = "eq_nao_encontrado"
IDENTIDADE_DUPLICADA = "identidade_duplicada"
LISTA_INVALIDA = "lista_invalida"
LISTA_DEMAIS = "lista_demais"
PERFIL_LONGO = "perfil_longo"

# The status of every stable code these routes answer; nothing else reaches the panel.
STATUS_POR_CODIGO = {
    CAMPO_INVALIDO: 400,
    CORPO_INVALIDO: 400,
    NAO_SUPORTADO: 400,
    modulo_varredura.FAIXA_INVALIDA: 400,
    modulo_varredura.FAIXA_PUBLICA: 400,
    modulo_varredura.FAIXA_GRANDE: 400,
    INVALID_VALUE: 400,
    IP_INVALIDO: 400,
    TIPO_DESCONHECIDO: 400,
    "nao_suportado": 400,
    LISTA_INVALIDA: 400,
    LISTA_DEMAIS: 400,
    PERFIL_LONGO: 400,
    mapa.PERFIS_LONGOS: 400,
    EQ_NAO_ENCONTRADO: 404,
    "auth_pendente": 409,
    IDENTIDADE_DUPLICADA: 409,
    modulo_numeros.PRODUTO_INCOMPATIVEL: 400,
    "erro_aparelho": 502,
    "eq_offline": 503,
    ERRO_INTERNO: 500,
}

_INTEIRO = re.compile(r"-?[0-9]+")


class _Recusa(Exception):
    """A stable code on the way out of the validation, so no handler builds a status."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def _erro(codigo: str) -> web.Response:
    return resposta_erro(STATUS_POR_CODIGO.get(codigo, 500), codigo)


def _gestor(request: web.Request) -> Gestor:
    return request.app[GESTOR]


def _identidade(request: web.Request) -> str:
    return request.match_info["identidade"]


def _manifestos(app: web.Application) -> dict[str, Manifesto]:
    return {tipo: classe.MANIFESTO for tipo, classe in drivers_de(app).items()}


def _texto(bruto: object) -> str:
    """A printable string within the ceiling, or campo_invalido; the caller never guesses."""
    # This value ends up in config.json, in the panel and, for a field, in the bytes a
    # driver puts on the wire, so a control character is refused where it is typed.
    if not isinstance(bruto, str) or len(bruto) > TEXTO_MAXIMO or not bruto.isprintable():
        raise _Recusa(CAMPO_INVALIDO)
    return bruto


def _campos(
    manifesto: Manifesto, brutos: object, anterior: Cadastro | None
) -> tuple[dict[str, str], dict[str, str]]:
    """Splits what the panel sent into config fields and device credentials."""
    if not isinstance(brutos, dict):
        raise _Recusa(CAMPO_INVALIDO)
    declarados = {campo.nome: campo for campo in manifesto.config_campos}
    # A name the manifest does not declare is either a typo or someone using config.json
    # as free storage, and neither belongs in the registration of an equipment.
    if not set(brutos) <= set(declarados):
        raise _Recusa(CAMPO_INVALIDO)
    # An absent field keeps the stored value, secret or not, because an update that only
    # fixes the name would otherwise send the device back to the default of the manifest.
    guardados = anterior.campos if anterior is not None else {}
    guardados_segredos = anterior.segredos if anterior is not None else {}
    campos: dict[str, str] = {}
    segredos: dict[str, str] = {}
    for nome, campo in declarados.items():
        presente = nome in brutos
        if campo.tipo is TipoCampo.SEGREDO:
            valor = _texto(brutos[nome]) if presente else guardados_segredos.get(nome, "")
            if valor:
                segredos[nome] = valor
        else:
            valor = _texto(brutos[nome]).strip() if presente else guardados.get(nome, "")
            if valor and campo.tipo is TipoCampo.INTEIRO and not _INTEIRO.fullmatch(valor):
                raise _Recusa(CAMPO_INVALIDO)
            if valor:
                campos[nome] = valor
        if campo.obrigatorio and not valor:
            raise _Recusa(CAMPO_INVALIDO)
    return campos, segredos


def _montar_cadastro(
    dados: dict, manifestos: dict[str, Manifesto], identidade: str | None, anterior: Cadastro | None
) -> Cadastro:
    """The body as a Cadastro, or a _Recusa naming the field that refused it."""
    tipo = dados.get("tipo")
    manifesto = manifestos.get(tipo) if isinstance(tipo, str) else None
    if manifesto is None:
        raise _Recusa(TIPO_DESCONHECIDO)
    endereco = _endereco_de(dados.get("ip"), manifesto)
    campos, segredos = _campos(manifesto, dados.get("campos", {}), anterior)
    cadastro = Cadastro(
        identidade=_identidade_do_corpo(dados, identidade),
        tipo=manifesto.tipo,
        nome=_texto(dados.get("nome", "")).strip(),
        ip=endereco,
        campos=campos,
        segredos=segredos,
        listas=_listas(dados.get("listas"), anterior, manifesto),
        nivel_maximo=_nivel_maximo(dados.get("nivel_maximo"), anterior),
    )
    # The profile of an equipment fits 200 bytes on any number, and what does
    # not fit is refused where it is typed instead of leaving the panel of the platform blank.
    if not perfil.cabe_em_qualquer_numero(cadastro, manifesto):
        raise _Recusa(PERFIL_LONGO)
    return cadastro


def _nivel_maximo(bruto: object, anterior: Cadastro | None) -> int:
    """The ceiling of the level: absent keeps the stored one, a number from 1 to 100 sets it."""
    if bruto is None:
        return anterior.nivel_maximo if anterior is not None else NIVEL_MAXIMO
    if isinstance(bruto, str) and _INTEIRO.fullmatch(bruto.strip()):
        bruto = int(bruto.strip())
    if type(bruto) is not int or not 1 <= bruto <= NIVEL_MAXIMO:
        raise _Recusa(CAMPO_INVALIDO)
    return bruto


def _endereco_de(bruto: object, manifesto: Manifesto) -> str:
    """The address of the equipment, which a cloud driver does not have.

    Keeps the hub from becoming a proxy of the LAN by taking nothing but an ip
    literal, and says a device with no local API is reached through the cloud of its
    maker, where there is no address to take. A driver that is not a cloud one keeps the rule
    exactly as it was, so nothing else in the installation loosens.
    """
    if manifesto.nuvem:
        if bruto not in (None, ""):
            # An address on a cloud registration is somebody expecting the hub to dial it,
            # and it never will; refusing it is what keeps that expectation from being silent.
            raise _Recusa(IP_INVALIDO)
        return ""
    endereco = ip_literal(bruto)
    if endereco is None:
        raise _Recusa(IP_INVALIDO)
    return endereco


def _listas(
    brutas: object, anterior: Cadastro | None, manifesto: Manifesto
) -> dict[str, tuple[Item, ...]]:
    """The lists as the body sent them; the stored ones on an update that sent
    none, and what the driver suggests on a registration that sent none.

    The value of a shortcut is a string of the protocol of the device, so an equipment
    that arrives with an empty list leaves the integrator guessing what to type. A driver that
    suggests items hands over three that work, and clearing them stays possible because an
    update that sends an empty object is a body that sent lists.
    """
    if brutas is None:
        if anterior is not None:
            return dict(anterior.listas)
        return {
            lista: tuple(Item(rotulo=s.rotulo, valor=s.valor) for s in itens)
            for lista, itens in sugestoes_por_lista(manifesto).items()
        }
    if not isinstance(brutas, dict) or not set(brutas) <= set(LISTAS):
        raise _Recusa(LISTA_INVALIDA)
    listas: dict[str, tuple[Item, ...]] = {}
    for nome, entradas in brutas.items():
        if not isinstance(entradas, list):
            raise _Recusa(LISTA_INVALIDA)
        if len(entradas) > LISTAS_MAXIMO[nome]:
            raise _Recusa(LISTA_DEMAIS)
        itens = []
        for entrada in entradas:
            if not isinstance(entrada, dict) or not set(entrada) <= {"rotulo", "valor"}:
                raise _Recusa(LISTA_INVALIDA)
            rotulo = entrada.get("rotulo")
            valor = entrada.get("valor")
            if not isinstance(rotulo, str) or not isinstance(valor, str):
                raise _Recusa(LISTA_INVALIDA)
            rotulo = rotulo.strip()
            valor = valor.strip()
            if not item_valido(rotulo, valor):
                raise _Recusa(LISTA_INVALIDA)
            itens.append(Item(rotulo=rotulo, valor=valor))
        if itens:
            listas[nome] = tuple(itens)
    return listas


def _identidade_do_corpo(dados: dict, identidade: str | None) -> str:
    """On an update the path is the key; the body may repeat it but never change it."""
    bruto = dados.get("identidade")
    if identidade is None:
        nova = _texto(bruto).strip()
        if not nova:
            raise _Recusa(CAMPO_INVALIDO)
        return nova
    if bruto is not None and _texto(bruto).strip() != identidade:
        raise _Recusa(CAMPO_INVALIDO)
    return identidade


def _persistir(
    app: web.Application,
    cadastros: tuple[Cadastro, ...],
    numeros: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    # The route writes the set and answers whether it could, because a gestor
    # changed first left the daemon polling an equipment that never reached the disk, listed by
    # the panel until a restart made it vanish.
    atual = config_de(app)
    mudanca = {"equipamentos": cadastros}
    if numeros is not None:
        mudanca["numeros"] = numeros
    try:
        trocar_config(app, replace(atual, **mudanca))
    except OSError as erro:
        log.error("could not write the equipment set: %s", erro)
        return False
    return True


def _com(cadastros: tuple[Cadastro, ...], cadastro: Cadastro) -> tuple[Cadastro, ...]:
    # An update replaces the entry in place, so the list does not jump after a restart.
    if _achar(cadastros, cadastro.identidade) is None:
        return (*cadastros, cadastro)
    return tuple(cadastro if c.identidade == cadastro.identidade else c for c in cadastros)


def _achar(cadastros: tuple[Cadastro, ...], identidade: str | None) -> Cadastro | None:
    return next((c for c in cadastros if c.identidade == identidade), None)


@com_sessao
async def rede(request: web.Request) -> web.Response:
    """The private networks of the host, for the panel to prefill the range of the sweep."""
    return resposta_ok(faixas=list(rede_local.redes_locais()))


@com_sessao
async def catalogo(request: web.Request) -> web.Response:
    manifestos = _manifestos(request.app).values()
    return resposta_ok(catalogo=[manifesto_json(manifesto) for manifesto in manifestos])


@com_sessao
async def listar(request: web.Request) -> web.Response:
    gestor = _gestor(request)
    manifestos = _manifestos(request.app)
    estados = gestor.estados()
    livro = licencas_de(request.app)
    return resposta_ok(
        equipamentos=[
            equipamento_json(
                cadastro,
                manifestos.get(cadastro.tipo),
                estados[cadastro.identidade],
                livro.onde(cadastro.identidade),
            )
            for cadastro in gestor.cadastros
        ]
    )


@com_sessao
async def cadastrar(request: web.Request) -> web.Response:
    return await _gravar(request, None)


@com_sessao
async def atualizar(request: web.Request) -> web.Response:
    """A field the body omits keeps the stored value, secret or not; an empty string erases it."""
    return await _gravar(request, _identidade(request))


async def _gravar(request: web.Request, identidade: str | None) -> web.Response:
    app = request.app
    gestor = _gestor(request)
    dados = await ler_corpo(request)
    if dados is None:
        return _erro(CORPO_INVALIDO)
    anterior = _achar(gestor.cadastros, identidade)
    if identidade is not None and anterior is None:
        return _erro(EQ_NAO_ENCONTRADO)
    try:
        cadastro = _montar_cadastro(dados, _manifestos(app), identidade, anterior)
    except _Recusa as recusa:
        return _erro(recusa.codigo)
    if identidade is None and _achar(gestor.cadastros, cadastro.identidade) is not None:
        return _erro(IDENTIDADE_DUPLICADA)
    # The profiles of a licence share five strings, so an edited registration
    # that would push the licence past them is refused now, with the integrator at the
    # keyboard, instead of taking every profile of the licence off the bus.
    if not licencas_de(app).perfis_cabem(cadastro):
        return _erro(mapa.PERFIS_LONGOS)
    # An equipment only enters a licence of its product, so a change of tipo
    # that would move it to the other product while it holds a number is refused now instead
    # of emptying the number in silence on the next boot.
    onde = licencas_de(app).onde(cadastro.identidade)
    manifesto = _manifestos(app).get(cadastro.tipo)
    if onde is not None and manifesto is not None:
        if produto_de(manifesto.categoria) != licencas_de(app).produto_de(onde[0]):
            return _erro(modulo_numeros.PRODUTO_INCOMPATIVEL)
    # Any registered equipment may occupy a number, so a change of tipo keeps
    # the number: the data points follow the new manifest on the next report.
    if not _persistir(app, _com(gestor.cadastros, cadastro)):
        return _erro(ERRO_INTERNO)
    mudar = gestor.cadastrar if identidade is None else gestor.atualizar_cadastro
    try:
        await mudar(cadastro)
    except ErroDeCadastro as erro:
        return _erro(erro.codigo)
    return resposta_ok()


@com_sessao
async def remover(request: web.Request) -> web.Response:
    gestor = _gestor(request)
    identidade = _identidade(request)
    if _achar(gestor.cadastros, identidade) is None:
        return _erro(EQ_NAO_ENCONTRADO)
    restantes = tuple(c for c in gestor.cadastros if c.identidade != identidade)
    livro = licencas_de(request.app)
    # Numbers by position, so the number of a removed equipment stays there,
    # empty; closing the hole would move every equipment below it one number up, in silence,
    # on a bus the customer already automated. The group it was in falls with it, because a
    # group led by an equipment nobody has is a group nobody can take down.
    numeros = {
        chave: modulo_numeros.sem(ordem, identidade) for chave, ordem in livro.numeros().items()
    }
    if not _persistir(request.app, restantes, numeros):
        return _erro(ERRO_INTERNO)
    await livro.esquecer(identidade)
    await gestor.remover(identidade)
    return resposta_ok()


@com_sessao
async def acao(request: web.Request) -> web.Response:
    dados = await ler_corpo(request)
    if dados is None:
        return _erro(CORPO_INVALIDO)
    nome = dados.get("acao")
    if not isinstance(nome, str) or not nome:
        return _erro(CORPO_INVALIDO)
    valor = dados.get("valor")
    if not _valor_simples(valor):
        return _erro(INVALID_VALUE)
    identidade = _identidade(request)
    livro = licencas_de(request.app)
    # The volume, the transport and the radios of a speaker that follows a
    # master go to the master, and the book of licences is what knows who leads whom; a press
    # on the detail screen takes the same road a scene step and the bus take, so it never lands
    # on a slave that would refuse it or break the group. Only a slave takes that road: the
    # book serializes it behind every set of the licence, and a solo equipment or the master
    # answers for itself as fast as it always did.
    log.debug("painel: %s %s %r", identidade, nome, valor)
    if nome in ACOES_ROTEADAS and livro.segue_um_mestre(identidade):
        codigo = await livro.acionar(identidade, nome, valor)
        return resposta_ok() if codigo is None else _erro(_do_barramento(codigo))
    codigo = await _gestor(request).executar(identidade, nome, valor)
    return resposta_ok() if codigo is None else _erro(codigo)


def _do_barramento(codigo: str) -> str:
    """The code of the bus in the vocabulary of this route, so a refusal through the master
    answers the same status the direct road answers.
    """
    if codigo == protocolo.NUMERO_OFFLINE:
        return "eq_offline"
    if codigo == protocolo.VALOR_INVALIDO:
        return INVALID_VALUE
    return codigo


def _valor_simples(valor: object) -> bool:
    """One JSON scalar, because an action carries a level, a name or a switch and no more."""
    # A driver writes this value on a socket, so an object or a list would reach code
    # that expects a scalar; a NaN, which the JSON parser does accept, would reach arithmetic.
    if isinstance(valor, float):
        return isfinite(valor)
    return valor is None or isinstance(valor, str | int | bool)


@com_sessao
async def autenticar(request: web.Request) -> web.Response:
    identidade = _identidade(request)
    try:
        resultado = await _gestor(request).autenticar(identidade)
    except ErroDeCadastro as erro:
        return _erro(erro.codigo)
    if resultado == PAREADO:
        await _guardar_credenciais(request, identidade)
    return resposta_ok(resultado=resultado)


async def _guardar_credenciais(request: web.Request, identidade: str) -> None:
    """The credential the device handed over, written into the registration that owns it.

    Without this the pairing lives in the memory of the driver and dies with the daemon,
    so the person walks to the television again on every deploy, which is exactly what an LG
    did on the bench. The registration is the only place a hub remembers anything, and a field
    the manifest declares SEGREDO goes to the secrets, where keeps it and where the
    panel never reads it back. Failing to write is logged and never raised: the pairing DID
    happen, and answering falhou for a file that did not save would send the operator back to
    the television for nothing.
    """
    gestor = _gestor(request)
    achadas = gestor.credenciais(identidade)
    if not achadas:
        return
    anterior = _achar(gestor.cadastros, identidade)
    manifesto = _manifestos(request.app).get(anterior.tipo) if anterior is not None else None
    if anterior is None or manifesto is None:
        return
    segredos_do_manifesto = {
        campo.nome for campo in manifesto.config_campos if campo.tipo is TipoCampo.SEGREDO
    }
    campos = dict(anterior.campos)
    segredos = dict(anterior.segredos)
    mudou = False
    for nome, valor in achadas.items():
        onde = segredos if nome in segredos_do_manifesto else campos
        if onde.get(nome) == valor:
            continue
        onde[nome] = valor
        mudou = True
    if not mudou:
        return
    cadastro = replace(anterior, campos=campos, segredos=segredos)
    if not _persistir(request.app, _com(gestor.cadastros, cadastro)):
        log.error("equipment %s paired and the credential could not be written", identidade)
        return
    # The driver read its fields when it was born, so it is rebuilt to see the key it
    # just earned; the registration that follows is silent, with no dialog on the screen.
    await gestor.atualizar_cadastro(cadastro)
    log.info("equipment %s paired and its credential was written to the registration", identidade)


@com_sessao
async def aparelhos_da_conta(request: web.Request) -> web.Response:
    """What the credentials the operator just typed can reach, before any
    registration exists.

    A driver of the cloud of a maker commands one device out of an account, named by an id
    that exists only inside that account. Asking the operator to find that id and type it is
    asking him to do what the account already answers; this route asks, and the panel shows him
    the list to pick from. The credentials arrive in the body, are used for this one call and
    are thrown away with the driver: nothing is written and nothing is logged, because a token
    is a secret even while it is being tested.
    """
    tipo = request.match_info["tipo"]
    manifesto = _manifestos(request.app).get(tipo)
    classe = request.app[CATALOGO].drivers.get(tipo)
    if manifesto is None or classe is None:
        return _erro(EQ_NAO_ENCONTRADO)
    if not manifesto.lista_da_conta:
        return _erro(NAO_SUPORTADO)
    dados = await ler_corpo(request)
    if dados is None:
        return _erro(CORPO_INVALIDO)
    try:
        campos, segredos = _campos(manifesto, dados.get("campos", {}), None)
    except _Recusa as recusa:
        return _erro(recusa.codigo)
    driver = classe(
        Cadastro(identidade="listagem", tipo=tipo, nome="", ip="", campos=campos, segredos=segredos)
    )
    try:
        async with asyncio.timeout(LIMITE_DA_CONTA_S):
            achados = await driver.aparelhos_da_conta()
    except Exception as erro:
        log.warning("the account of a %s answered nothing: %s", tipo, type(erro).__name__)
        return _erro(AUTH_PENDENTE)
    finally:
        with suppress(Exception):
            await driver.parar()
    return resposta_ok(
        campo=manifesto.lista_da_conta,
        aparelhos=[{"id": aparelho.id, "nome": aparelho.nome} for aparelho in achados],
    )


@com_sessao
async def varredura(request: web.Request) -> web.Response:
    # This route was a POST with no body and stays one, because a sweep with no range
    # is the sweep it always did; an absent body is not a malformed body.
    corpo = await ler_corpo(request)
    # The range is optional because the multicast sweep is what a network with multicast
    # answers, and asking the operator to name his own network before he can press a button
    # would be a step nobody needs there. Where multicast does not cross, the range is the
    # only way, and says it is his to name.
    bruto = corpo.get("faixa") if isinstance(corpo, dict) else None
    rede = None
    if bruto not in (None, ""):
        try:
            rede = modulo_varredura.ler_faixa(bruto)
        except modulo_varredura.FaixaRecusada as recusa:
            return _erro(recusa.codigo)
    resultado = await _achados(request.app)
    if isinstance(resultado, str) and rede is None:
        return _erro(resultado)
    if rede is not None:
        da_faixa = await _varrer_faixa(request.app, rede)
        multicast = () if isinstance(resultado, str) else resultado
        resultado = _sem_repetir(multicast + da_faixa)
    cadastros = _gestor(request).cadastros
    identidades = {cadastro.identidade for cadastro in cadastros}
    enderecos = {cadastro.ip for cadastro in cadastros}
    no_endereco = {(cadastro.tipo, cadastro.ip) for cadastro in cadastros if cadastro.ip}

    def conhecido(achado: descoberta.Achado) -> bool:
        # An answer with no uuid is only recognizable by the address it came from, which
        # is all the segment gave us; with a uuid, the identity is what decides. Except that a
        # registration written by hand may name the same device another way: on 7/set/2026 a
        # television registered by its MAC answered the sweep with its uuid, and the button
        # offered to register it again. A driver of the same type already registered at the
        # address the answer came from is that device, whatever name the person gave it.
        if achado.identidade and achado.identidade in identidades:
            return True
        if achado.tipo and (achado.tipo, achado.ip) in no_endereco:
            return True
        return not achado.identidade and achado.ip in enderecos

    return resposta_ok(
        achados=[achado_json(achado, ja_cadastrado=conhecido(achado)) for achado in resultado]
    )


async def _varrer_faixa(app: web.Application, rede: object) -> tuple[descoberta.Achado, ...]:
    """One range sweep at a time, shared: a panel that gave up waiting and asked again must
    not put a second sweep of the same range on top of the first."""
    corrida = app[VARREDURA_FAIXA]
    em_curso = corrida.valor
    if em_curso is not None and em_curso[0] == str(rede) and not em_curso[1].done():
        return await asyncio.shield(em_curso[1])
    tarefa = asyncio.create_task(_varrer_a_faixa(app, rede), name="descoberta-faixa")
    corrida.valor = (str(rede), tarefa)
    return await asyncio.shield(tarefa)


async def _varrer_a_faixa(app: web.Application, rede: object) -> tuple[descoberta.Achado, ...]:
    """The range sweep itself, which answers nothing instead of an error: the multicast half
    of the answer is still worth showing.
    """
    try:
        async with asyncio.timeout(LIMITE_FAIXA_S):
            return await modulo_varredura.procurar(
                rede, app[CATALOGO].drivers, sondas=presenca.Sondas()
            )
    except (TimeoutError, OSError) as erro:
        log.warning("range sweep of %s failed: %s", rede, erro)
        return ()


def _sem_repetir(achados: tuple[descoberta.Achado, ...]) -> tuple[descoberta.Achado, ...]:
    """One row per device, built from every sighting of it.

    On a segment one address is one device, and the same speaker is seen more than once: it
    announces itself by multicast without naming a driver, and it answers its own port naming
    one. Two rows of it is the operator asking himself which of the two is the real one, so
    the sightings of an address become a single row that keeps what each of them saw. An
    identity seen twice is one device that changed address while the sweep ran, and the first
    sighting stays.
    """
    por_endereco: dict[str, descoberta.Achado] = {}
    for achado in achados:
        anterior = por_endereco.get(achado.ip)
        por_endereco[achado.ip] = achado if anterior is None else _fundir(anterior, achado)
    resultado: list[descoberta.Achado] = []
    identidades: set[str] = set()
    for achado in por_endereco.values():
        if achado.identidade and achado.identidade in identidades:
            continue
        identidades.add(achado.identidade)
        resultado.append(achado)
    return tuple(resultado)


# A description a probe wrote when it had nothing better: the open ports, or the MAC of an
# address that answered no port at all. It is what shows when nobody named the device.
_DE_SONDA = ("TCP ", "MAC ")


def _fundir(um: descoberta.Achado, outro: descoberta.Achado) -> descoberta.Achado:
    """Two sightings of one address as one row: the one that names a driver decides the type,
    and what only the other saw is kept."""
    melhor, resto = (um, outro) if um.tipo else (outro, um)
    return replace(
        melhor,
        identidade=melhor.identidade or resto.identidade,
        porta=melhor.porta if melhor.porta is not None else resto.porta,
        nome=melhor.nome or resto.nome,
        descricao=_melhor_descricao(melhor.descricao, resto.descricao),
    )


def _melhor_descricao(uma: str, outra: str) -> str:
    """A name the device gave itself beats the list of ports a probe wrote for it.

    The speaker that announces itself as "Sala" and answers its own port is one row, and the
    row that says Sala is worth more to the person reading it than one that says TCP 80.
    """
    candidatas = [texto for texto in (uma, outra) if texto]
    nomeadas = [texto for texto in candidatas if not texto.startswith(_DE_SONDA)]
    return (nomeadas or candidatas or [""])[0]


async def _achados(app: web.Application) -> tuple[descoberta.Achado, ...] | str:
    """One sweep at a time: a second request rides the one already on the segment."""
    # Every sweep floods the segment with M-SEARCH, so two panels open at once must not
    # multiply the traffic; the answer of the sweep in flight is the same answer for both.
    corrida = app[VARREDURA]
    tarefa = corrida.valor
    if tarefa is None or tarefa.done():
        tarefa = asyncio.create_task(_varrer(app), name="descoberta")
        corrida.valor = tarefa
    # The panel giving up must not cancel a sweep another panel is waiting for.
    return await asyncio.shield(tarefa)


async def _varrer(app: web.Application) -> tuple[descoberta.Achado, ...] | str:
    """The sweep itself, which answers a stable code instead of letting a socket raise."""
    try:
        plano = descoberta.montar(_manifestos(app).values())
    except descoberta.PlanoAmbiguo as erro:
        log.warning("discovery sweep failed: %s", erro)
        return ERRO_INTERNO
    try:
        async with asyncio.timeout(LIMITE_VARREDURA_S):
            # Generates the discovery from the manifests, and a manifest
            # declares its signature on the transport its device answers: the multiroom
            # speaker is only ever found by mDNS, and sweeping SSDP alone made
            # the panel answer "nothing here" on a segment full of speakers. The two run
            # together because the sweep floods the segment either way and its length is the
            # slower of the two, not their sum.
            ssdp, mdns = await asyncio.gather(
                descoberta.procurar(plano, destino=DESTINO, timeout_s=TIMEOUT_VARREDURA_S),
                descoberta.procurar_mdns(
                    plano, destino=DESTINO_MDNS, timeout_s=TIMEOUT_VARREDURA_S
                ),
                return_exceptions=True,
            )
    except TimeoutError as erro:
        log.warning("discovery sweep failed: %s", erro)
        return ERRO_INTERNO
    juntos = _juntar(ssdp, mdns)
    if isinstance(juntos, str):
        return juntos
    return await _identificar(app, juntos)


# The mDNS answer of a speaker carries its name and its address and not its uuid, and
# registers the uuid; without this the sweep found the speaker and the panel still
# asked the operator to type its identity by hand.
LIMITE_IDENTIFICACAO_S = 4.0
IDENTIFICACOES_AO_MESMO_TEMPO = 8


async def _identificar(
    app: web.Application, achados: tuple[descoberta.Achado, ...]
) -> tuple[descoberta.Achado, ...]:
    classes = app[CATALOGO].drivers
    vaga = asyncio.Semaphore(IDENTIFICACOES_AO_MESMO_TEMPO)

    async def um(achado: descoberta.Achado) -> descoberta.Achado:
        classe = classes.get(achado.tipo) if achado.tipo and not achado.identidade else None
        if classe is None:
            return achado
        try:
            async with vaga, asyncio.timeout(LIMITE_IDENTIFICACAO_S):
                identidade = await classe.identificar(achado.ip)
        except Exception as erro:
            log.warning("could not identify %s at %s: %s", achado.tipo, achado.ip, erro)
            return achado
        return achado if not identidade else replace(achado, identidade=identidade)

    return tuple(await asyncio.gather(*(um(achado) for achado in achados)))


def _juntar(
    ssdp: tuple[descoberta.Achado, ...] | BaseException,
    mdns: tuple[descoberta.Achado, ...] | BaseException,
) -> tuple[descoberta.Achado, ...] | str:
    """The findings of both transports, with a device seen on both counted once.

    One transport failing is a fault of this host on that transport, and throwing away
    what the other one found would hide the speakers the panel is there to show. Both failing
    is the fault wants reported, because answering an empty list would send the
    integrator hunting the network instead of the daemon.
    """
    achados: list[descoberta.Achado] = []
    falhas = 0
    for resultado in (ssdp, mdns):
        if isinstance(resultado, BaseException):
            falhas += 1
            log.warning("discovery sweep failed: %s", resultado)
            continue
        achados.extend(resultado)
    if falhas == 2:
        return ERRO_INTERNO
    vistos: dict[tuple[str, str], descoberta.Achado] = {}
    for achado in achados:
        # The same speaker answering both transports is one device, and says
        # the identity decides; only an answer with no identity falls back to the address.
        chave = ("id", achado.identidade) if achado.identidade else ("ip", achado.ip)
        vistos.setdefault(chave, achado)
    return tuple(vistos.values())
