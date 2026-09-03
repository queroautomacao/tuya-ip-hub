# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Equipment routes: the catalog, the registrations, one action and the discovery sweep.

The gestor imposes the rules of section 6 and answers a stable code; these routes only
turn that code into a status and never invent one of their own. The address a route takes
is an IP literal (section 9), so the hub never reaches a host nobody registered.

Rotas de equipamento: o catálogo, os cadastros, uma ação e a varredura de descoberta.

O gestor impõe as regras da seção 6 e responde um código estável; estas rotas só
transformam esse código num status e nunca inventam um próprio. O endereço que uma rota
recebe é um IP literal (seção 9), então o hub nunca alcança um host que ninguém cadastrou.
"""

import asyncio
import logging
import re
from dataclasses import replace
from math import isfinite

from aiohttp import web

from iphub.api.comum import (
    GESTOR,
    VARREDURA,
    com_sessao,
    config_de,
    drivers_de,
    ler_corpo,
    resposta_ok,
    trocar_config,
)
from iphub.api.formato import achado_json, equipamento_json, manifesto_json
from iphub.config import Cadastro, ip_literal
from iphub.drivers import descoberta
from iphub.drivers.gestor import ErroDeCadastro, Gestor
from iphub.drivers.manifesto import Manifesto, TipoCampo
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.equipamentos")

# Why: the sweep waits for the segment to answer, and the hard limit is what keeps a socket
# that never returns from holding the route open until the panel gives up.
# Por que: a varredura espera o segmento responder, e o limite duro é o que impede um socket
# que nunca volta de segurar a rota até o painel desistir.
TIMEOUT_VARREDURA_S = 3.0
LIMITE_VARREDURA_S = 8.0
DESTINO = descoberta.DESTINO_PADRAO

TEXTO_MAXIMO = 200

CORPO_INVALIDO = "corpo_invalido"
CAMPO_INVALIDO = "campo_invalido"
TIPO_DESCONHECIDO = "tipo_desconhecido"
IP_INVALIDO = "ip_invalido"
INVALID_VALUE = "invalid_value"
ERRO_INTERNO = "erro_interno"
EQ_NAO_ENCONTRADO = "eq_nao_encontrado"
IDENTIDADE_DUPLICADA = "identidade_duplicada"

# The status of every stable code these routes answer; nothing else reaches the panel.
# O status de todo código estável que estas rotas respondem; nada mais chega ao painel.
STATUS_POR_CODIGO = {
    CAMPO_INVALIDO: 400,
    CORPO_INVALIDO: 400,
    INVALID_VALUE: 400,
    IP_INVALIDO: 400,
    TIPO_DESCONHECIDO: 400,
    "nao_suportado": 400,
    EQ_NAO_ENCONTRADO: 404,
    "auth_pendente": 409,
    IDENTIDADE_DUPLICADA: 409,
    "erro_aparelho": 502,
    "eq_offline": 503,
    ERRO_INTERNO: 500,
}

_INTEIRO = re.compile(r"-?[0-9]+")


class _Recusa(Exception):
    """A stable code on the way out of the validation, so no handler builds a status.

    Um código estável na saída da validação, para nenhum handler montar um status.
    """

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
    """A printable string within the ceiling, or campo_invalido; the caller never guesses.

    Um texto imprimível dentro do teto, ou campo_invalido; quem chama nunca adivinha.
    """
    # Why: this value ends up in config.json, in the panel and, for a field, in the bytes a
    # driver puts on the wire, so a control character is refused where it is typed.
    # Por que: este valor termina no config.json, no painel e, num campo, nos bytes que um
    # driver põe no fio, então um caractere de controle é recusado onde ele é digitado.
    if not isinstance(bruto, str) or len(bruto) > TEXTO_MAXIMO or not bruto.isprintable():
        raise _Recusa(CAMPO_INVALIDO)
    return bruto


def _campos(
    manifesto: Manifesto, brutos: object, anterior: Cadastro | None
) -> tuple[dict[str, str], dict[str, str]]:
    """Splits what the panel sent into config fields and device credentials.

    Separa o que o painel mandou entre campos de configuração e credenciais de aparelho.
    """
    if not isinstance(brutos, dict):
        raise _Recusa(CAMPO_INVALIDO)
    declarados = {campo.nome: campo for campo in manifesto.config_campos}
    # Why: a name the manifest does not declare is either a typo or someone using config.json
    # as free storage, and neither belongs in the registration of an equipment.
    # Por que: um nome que o manifesto não declara é engano ou alguém usando o config.json
    # como armazenamento livre, e nenhum dos dois pertence ao cadastro de um equipamento.
    if not set(brutos) <= set(declarados):
        raise _Recusa(CAMPO_INVALIDO)
    # Why: an absent field keeps the stored value, secret or not, because an update that only
    # fixes the name would otherwise send the device back to the default of the manifest.
    # Por que: um campo ausente mantém o valor guardado, segredo ou não, porque senão uma
    # atualização que só conserta o nome devolveria o aparelho ao padrão do manifesto.
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
    """The body as a Cadastro, or a _Recusa naming the field that refused it.

    O corpo como Cadastro, ou uma _Recusa nomeando o campo que o recusou.
    """
    tipo = dados.get("tipo")
    manifesto = manifestos.get(tipo) if isinstance(tipo, str) else None
    if manifesto is None:
        raise _Recusa(TIPO_DESCONHECIDO)
    endereco = ip_literal(dados.get("ip"))
    if endereco is None:
        raise _Recusa(IP_INVALIDO)
    campos, segredos = _campos(manifesto, dados.get("campos", {}), anterior)
    return Cadastro(
        identidade=_identidade_do_corpo(dados, identidade),
        tipo=manifesto.tipo,
        nome=_texto(dados.get("nome", "")).strip(),
        ip=endereco,
        campos=campos,
        segredos=segredos,
    )


def _identidade_do_corpo(dados: dict, identidade: str | None) -> str:
    """On an update the path is the key; the body may repeat it but never change it.

    Numa atualização o caminho é a chave; o corpo pode repeti-la mas nunca trocá-la.
    """
    bruto = dados.get("identidade")
    if identidade is None:
        nova = _texto(bruto).strip()
        if not nova:
            raise _Recusa(CAMPO_INVALIDO)
        return nova
    if bruto is not None and _texto(bruto).strip() != identidade:
        raise _Recusa(CAMPO_INVALIDO)
    return identidade


def _persistir(app: web.Application, cadastros: tuple[Cadastro, ...]) -> bool:
    # Why: the route writes the set (section 6) and answers whether it could, because a gestor
    # changed first left the daemon polling an equipment that never reached the disk, listed by
    # the panel until a restart made it vanish.
    # Por que: a rota grava o conjunto (seção 6) e responde se conseguiu, porque um gestor
    # alterado primeiro deixava o daemon com um equipamento que nunca chegou ao disco, listado
    # pelo painel até um reinício o sumir.
    try:
        trocar_config(app, replace(config_de(app), equipamentos=cadastros))
    except OSError as erro:
        log.error("could not write the equipment set: %s", erro)
        return False
    return True


def _com(cadastros: tuple[Cadastro, ...], cadastro: Cadastro) -> tuple[Cadastro, ...]:
    # Why: an update replaces the entry in place, so the list does not jump after a restart.
    # Por que: uma atualização troca a entrada no lugar, para a lista não pular após reinício.
    if _achar(cadastros, cadastro.identidade) is None:
        return (*cadastros, cadastro)
    return tuple(cadastro if c.identidade == cadastro.identidade else c for c in cadastros)


def _achar(cadastros: tuple[Cadastro, ...], identidade: str | None) -> Cadastro | None:
    return next((c for c in cadastros if c.identidade == identidade), None)


@com_sessao
async def catalogo(request: web.Request) -> web.Response:
    manifestos = _manifestos(request.app).values()
    return resposta_ok(catalogo=[manifesto_json(manifesto) for manifesto in manifestos])


@com_sessao
async def listar(request: web.Request) -> web.Response:
    gestor = _gestor(request)
    manifestos = _manifestos(request.app)
    estados = gestor.estados()
    return resposta_ok(
        equipamentos=[
            equipamento_json(cadastro, manifestos.get(cadastro.tipo), estados[cadastro.identidade])
            for cadastro in gestor.cadastros
        ]
    )


@com_sessao
async def cadastrar(request: web.Request) -> web.Response:
    return await _gravar(request, None)


@com_sessao
async def atualizar(request: web.Request) -> web.Response:
    """A field the body omits keeps the stored value, secret or not; an empty string erases it.

    Um campo que o corpo omite mantém o valor guardado, segredo ou não; um vazio o apaga.
    """
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
    if not _persistir(request.app, restantes):
        return _erro(ERRO_INTERNO)
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
    codigo = await _gestor(request).executar(_identidade(request), nome, valor)
    return resposta_ok() if codigo is None else _erro(codigo)


def _valor_simples(valor: object) -> bool:
    """One JSON scalar, because an action carries a level, a name or a switch and no more.

    Um escalar JSON, porque uma ação leva um nível, um nome ou uma chave e nada mais.
    """
    # Why: a driver writes this value on a socket, so an object or a list would reach code
    # that expects a scalar; a NaN, which the JSON parser does accept, would reach arithmetic.
    # Por que: um driver escreve este valor num socket, então um objeto ou lista chegaria a
    # código que espera escalar; um NaN, que o analisador JSON aceita, chegaria a uma conta.
    if isinstance(valor, float):
        return isfinite(valor)
    return valor is None or isinstance(valor, str | int | bool)


@com_sessao
async def autenticar(request: web.Request) -> web.Response:
    try:
        resultado = await _gestor(request).autenticar(_identidade(request))
    except ErroDeCadastro as erro:
        return _erro(erro.codigo)
    return resposta_ok(resultado=resultado)


@com_sessao
async def varredura(request: web.Request) -> web.Response:
    resultado = await _achados(request.app)
    if isinstance(resultado, str):
        return _erro(resultado)
    cadastros = _gestor(request).cadastros
    identidades = {cadastro.identidade for cadastro in cadastros}
    enderecos = {cadastro.ip for cadastro in cadastros}

    def conhecido(achado: descoberta.Achado) -> bool:
        # Why: an answer with no uuid is only recognizable by the address it came from, which
        # is all the segment gave us; with a uuid, the identity is what decides.
        # Por que: uma resposta sem uuid só é reconhecível pelo endereço de onde veio, que é
        # tudo que o segmento nos deu; com uuid, a identidade é quem decide.
        if achado.identidade:
            return achado.identidade in identidades
        return achado.ip in enderecos

    return resposta_ok(
        achados=[achado_json(achado, ja_cadastrado=conhecido(achado)) for achado in resultado]
    )


async def _achados(app: web.Application) -> tuple[descoberta.Achado, ...] | str:
    """One sweep at a time: a second request rides the one already on the segment.

    Uma varredura por vez: um segundo pedido pega carona na que já está no segmento.
    """
    # Why: every sweep floods the segment with M-SEARCH, so two panels open at once must not
    # multiply the traffic; the answer of the sweep in flight is the same answer for both.
    # Por que: toda varredura inunda o segmento com M-SEARCH, então dois painéis abertos ao
    # mesmo tempo não podem multiplicar o tráfego; a resposta da varredura em curso serve aos
    # dois.
    corrida = app[VARREDURA]
    tarefa = corrida.valor
    if tarefa is None or tarefa.done():
        tarefa = asyncio.create_task(_varrer(app), name="descoberta")
        corrida.valor = tarefa
    # Why: the panel giving up must not cancel a sweep another panel is waiting for.
    # Por que: o painel desistindo não pode cancelar uma varredura que outro painel espera.
    return await asyncio.shield(tarefa)


async def _varrer(app: web.Application) -> tuple[descoberta.Achado, ...] | str:
    """The sweep itself, which answers a stable code instead of letting a socket raise.

    A varredura em si, que responde um código estável em vez de deixar um socket estourar.
    """
    try:
        plano = descoberta.montar(_manifestos(app).values())
        async with asyncio.timeout(LIMITE_VARREDURA_S):
            return await descoberta.procurar(plano, destino=DESTINO, timeout_s=TIMEOUT_VARREDURA_S)
    except (OSError, TimeoutError, descoberta.PlanoAmbiguo) as erro:
        # Why: a segment with no route for the group is a fault of this host, and answering an
        # empty list would send the integrator hunting the network instead of the daemon.
        # Por que: um segmento sem rota para o grupo é falha deste host, e responder lista
        # vazia mandaria o integrador caçar a rede em vez do daemon.
        log.warning("discovery sweep failed: %s", erro)
        return ERRO_INTERNO
