# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 over HTTP: the order of the six blocks, the data points and the group.

A zone is what section 6 says it is, a multiroom equipment occupying one of the six blocks,
so these routes carry no second registry: the order is a list of identities already
registered as equipment, and the module of the zones is the one that judges it and answers a
stable code. Nothing here decides where a set goes either; aplicar_dp of the common module
does, so the panel and the bus of the same hub cannot disagree about it.

The numbers of section 8 leave the daemon in the answers instead of being written a second
time in the panel: each block carries the data point of each of its functions, and the
snapshot route carries the whole table. A panel that computed the numbering would be a
second copy of the contract, and the day the contract moved only one of them would move.

Seção 8 sobre HTTP: a ordem dos seis blocos, os data points e o grupo.

Uma zona é o que a seção 6 diz que ela é, um equipamento multiroom ocupando um dos seis
blocos, então estas rotas não carregam segundo cadastro: a ordem é uma lista de identidades
já cadastradas como equipamento, e o módulo das zonas é quem a julga e responde um código
estável. Nada aqui decide para onde vai um set tampouco; quem decide é o aplicar_dp do módulo
comum, para o painel e o barramento do mesmo hub não poderem discordar disso.

Os números da seção 8 saem do daemon nas respostas em vez de serem escritos uma segunda vez
no painel: cada bloco carrega o data point de cada função dele, e a rota de snapshot carrega
a tabela inteira. Um painel que calculasse a numeração seria uma segunda cópia do contrato, e
no dia em que o contrato mudasse só uma das duas mudaria.
"""

import logging
import re
from dataclasses import replace

from aiohttp import web

from iphub import cenas
from iphub.api.comum import (
    GESTOR,
    aplicar_dp,
    com_sessao,
    config_de,
    ler_corpo,
    resposta_ok,
    trocar_config,
    valores_dps,
    zonas_de,
)
from iphub.api.formato import estado_json
from iphub.config import Cadastro
from iphub.dpbus import mapa, protocolo
from iphub.dpbus import zonas as modulo
from iphub.drivers.manifesto import Estado
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.zonas")

_SO_DIGITOS = re.compile(r"[0-9]{1,10}")

CORPO_INVALIDO = "corpo_invalido"
ERRO_INTERNO = "erro_interno"

PAPEL_MESTRE = "mestre"
PAPEL_ESCRAVO = "escravo"
PAPEL_SOLO = ""

# The status of every stable code these routes answer; nothing else reaches the panel.
# O status de todo código estável que estas rotas respondem; nada mais chega ao painel.
STATUS_POR_CODIGO = {
    CORPO_INVALIDO: 400,
    modulo.ZONAS_DEMAIS: 400,
    modulo.ZONA_REPETIDA: 400,
    modulo.EQ_NAO_MULTIROOM: 400,
    modulo.IDENTIDADE_INVALIDA: 400,
    "eq_nao_encontrado": 404,
    protocolo.DP_DESCONHECIDO: 404,
    protocolo.DP_SOMENTE_LEITURA: 400,
    protocolo.VALOR_INVALIDO: 400,
    protocolo.ZONA_OFFLINE: 503,
    "nao_suportado": 400,
    "auth_pendente": 409,
    "erro_aparelho": 502,
    # Why: DP 131 runs a scene, so the two codes the scene executor answers reach this route
    # too, and without them the panel got a 500 with erro_interno for a scene that simply does
    # not exist or is already running, while the scenes route answered 404 and 409 for the
    # very same conditions.
    # Por que: o DP 131 executa uma cena, então os dois códigos que o executor de cenas
    # responde chegam também a esta rota, e sem eles o painel recebia um 500 com erro_interno
    # para uma cena que simplesmente não existe ou já está rodando, enquanto a rota de cenas
    # respondia 404 e 409 para as mesmíssimas condições.
    cenas.CENA_NAO_ENCONTRADA: 404,
    cenas.CENA_EM_CURSO: 409,
    ERRO_INTERNO: 500,
}


def erro(codigo: str) -> web.Response:
    """The stable code as the status the panel reads, and never a status invented here.

    O código estável como o status que o painel lê, e nunca um status inventado aqui.
    """
    return resposta_erro(STATUS_POR_CODIGO.get(codigo, 500), codigo)


def _cadastros(request: web.Request) -> dict[str, Cadastro]:
    return {cadastro.identidade: cadastro for cadastro in request.app[GESTOR].cadastros}


def _papel(zona: int, mestre: int, escravos: tuple[int, ...], alheios: tuple[int, ...]) -> str:
    """What the speaker of this block really is right now, and not only what the hub asked.

    Why: a speaker the customer grouped with the app of the manufacturer is a slave of a group
    this hub does not lead, and it refuses volume, transport, preset and input. Calling that
    solo drew a panel full of controls that only ever answer no, with nothing saying why.

    O que a caixa deste bloco realmente é agora, e não só o que o hub pediu.

    Por que: uma caixa que o cliente agrupou com o app do fabricante é escrava de um grupo que
    este hub não lidera, e ela recusa volume, transporte, preset e entrada. Chamar isso de solo
    desenhava um painel cheio de controles que só respondem não, sem nada dizendo por quê.
    """
    if mestre and zona == mestre:
        return PAPEL_MESTRE
    if zona in escravos or zona in alheios:
        return PAPEL_ESCRAVO
    return PAPEL_SOLO


def _entradas(zonas: modulo.Zonas, zona: int) -> tuple[str, ...]:
    """The inputs the bus really takes for one block, which the hardware decides.

    As entradas que o barramento realmente aceita para um bloco, que o hardware decide.
    """
    for dp in mapa.da_zona(zona):
        if dp.funcao == mapa.FUNCAO_ENTRADA:
            return zonas.valores_de(dp)
    return ()


def _dps_do_bloco(zona: int) -> dict[str, int]:
    """The data point of every function of one block, straight from the map of section 8.

    O data point de cada função de um bloco, direto do mapa da seção 8.
    """
    return {funcao: mapa.dp_de(zona, funcao) for funcao in mapa.FUNCOES_ZONA}


def _zona_json(
    zona: int,
    cadastro: Cadastro | None,
    estado: Estado | None,
    entradas: tuple[str, ...],
    papel: str,
) -> dict:
    """One block as the panel reads it: who occupies it, what it says and what it takes.

    Um bloco como o painel o lê: quem o ocupa, o que ele diz e o que ele aceita.
    """
    # Why: a block nobody occupies is a normal state of the hub (section 6 works with zero
    # equipment), so it answers with an empty identity and a null state instead of vanishing
    # from the list; the POSITION is the contract, and a shorter list would move it.
    # Por que: um bloco que ninguém ocupa é estado normal do hub (a seção 6 funciona com zero
    # equipamento), então ele responde com identidade vazia e estado nulo em vez de sumir da
    # lista; a POSIÇÃO é o contrato, e uma lista mais curta a moveria.
    return {
        "zona": zona,
        "identidade": "" if cadastro is None else cadastro.identidade,
        "nome": "" if cadastro is None else cadastro.nome,
        "tipo": "" if cadastro is None else cadastro.tipo,
        "papel": papel,
        "entradas": list(entradas),
        "dps": _dps_do_bloco(zona),
        "estado": None if estado is None else estado_json(estado),
    }


@com_sessao
async def listar(request: web.Request) -> web.Response:
    zonas = zonas_de(request.app)
    cadastros = _cadastros(request)
    estados = request.app[GESTOR].estados()
    mestre = modulo.zona_do_grupo(zonas.grupo()) or 0
    escravos = zonas.escravos()
    alheios = zonas.escravos_alheios()
    blocos = []
    for zona in range(1, mapa.ZONAS + 1):
        identidade = zonas.identidade(zona)
        blocos.append(
            _zona_json(
                zona,
                cadastros.get(identidade),
                estados.get(identidade),
                _entradas(zonas, zona),
                _papel(zona, mestre, escravos, alheios),
            )
        )
    return resposta_ok(zonas=blocos, grupo=zonas.grupo(), dp_grupo=mapa.GRUPO)


@com_sessao
async def definir(request: web.Request) -> web.Response:
    """Saves the order of the blocks, which is the only registry a zone ever has.

    Grava a ordem dos blocos, que é o único cadastro que uma zona tem.
    """
    app = request.app
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    zonas = zonas_de(app)
    try:
        ordem = zonas.validar(dados.get("zonas"))
    except modulo.OrdemInvalida as recusa:
        return erro(recusa.codigo)
    # Why: the file is written before the order is applied, because an order that lived only
    # in memory would move a speaker back to its old block on the next boot, in silence, on a
    # bus the customer already automated.
    # Por que: o arquivo é gravado antes de a ordem valer, porque uma ordem que vivesse só na
    # memória moveria uma caixa de volta ao bloco antigo no próximo boot, em silêncio, num
    # barramento que o cliente já automatizou.
    try:
        trocar_config(app, replace(config_de(app), zonas=ordem))
    except OSError as falha:
        log.error("could not write the order of the zones: %s", falha)
        return erro(ERRO_INTERNO)
    try:
        # Why: the module judges the order again under its own lock, so an equipment removed
        # between the two calls is a stable code and never a 500 with a traceback.
        # Por que: o módulo julga a ordem de novo sob a trava dele, então um equipamento
        # removido entre as duas chamadas é um código estável e nunca um 500 com traceback.
        await zonas.definir_ordem(ordem)
    except modulo.OrdemInvalida as recusa:
        return erro(recusa.codigo)
    return resposta_ok(zonas=list(ordem))


@com_sessao
async def dps(request: web.Request) -> web.Response:
    """Every reportable data point and the table of section 8, for the panel and for curl.

    Todo data point reportável e a tabela da seção 8, para o painel e para o curl.
    """
    quadro = protocolo.snapshot(valores_dps(request.app))
    zonas = zonas_de(request.app)
    tabela = [
        {
            "dpid": dp.dpid,
            "zona": dp.zona,
            "funcao": dp.funcao,
            "tipo": dp.tipo.value,
            "sentido": dp.sentido.value,
            # Why: the inputs of a speaker come from the hardware (section 14, plm_support),
            # so the values of that enum are the ones this hub really offers right now and
            # not a list the panel could guess from the contract.
            # Por que: as entradas de uma caixa vêm do hardware (seção 14, plm_support), então
            # os valores daquele enum são os que este hub realmente oferece agora e não uma
            # lista que o painel pudesse adivinhar do contrato.
            "valores": list(zonas.valores_de(dp)),
        }
        for dp in mapa.DPS
    ]
    return resposta_ok(dps=quadro["dps"], mapa=tabela)


@com_sessao
async def ajustar(request: web.Request) -> web.Response:
    """One set of section 8 by HTTP, with the same effect a set on the bus would have.

    Um set da seção 8 por HTTP, com o mesmo efeito que um set no barramento teria.
    """
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    codigo = await aplicar_dp(request.app, _dpid(request), dados.get("v"))
    return resposta_ok() if codigo is None else erro(codigo)


@com_sessao
async def grupo(request: web.Request) -> web.Response:
    """DP 132 by name: solo takes the group down, grupoN forms the one that zone leads.

    O DP 132 pelo nome: solo derruba o grupo, grupoN forma o que aquela zona lidera.
    """
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    codigo = await aplicar_dp(request.app, mapa.GRUPO, dados.get("v"))
    return resposta_ok(grupo=zonas_de(request.app).grupo()) if codigo is None else erro(codigo)


def _dpid(request: web.Request) -> object:
    """The number in the path, or the raw text when it is not a number at all.

    O número no caminho, ou o texto cru quando ele não é número nenhum.
    """
    # Why: the map answers dp_desconhecido for anything that is not an int, so a path of
    # letters travels as text and is refused by the same rule that refuses dp 999.
    # Por que: o mapa responde dp_desconhecido para o que não for int, então um caminho de
    # letras viaja como texto e é recusado pela mesma regra que recusa o dp 999.
    bruto = request.match_info["dpid"]
    # Why: str.isdigit() is true for characters int() refuses, such as the superscript two,
    # so a session holder could turn this route into a 500 with a traceback in the log. Only
    # an ASCII digit is a number here, and the ceiling keeps a path of a thousand digits from
    # becoming a thousand digit int.
    # Por que: str.isdigit() é verdadeiro para caracteres que o int() recusa, como o dois
    # sobrescrito, então quem tem sessão podia transformar esta rota num 500 com traceback no
    # log. Só dígito ASCII é número aqui, e o teto impede que um caminho de mil dígitos vire
    # um int de mil dígitos.
    return int(bruto) if _SO_DIGITOS.fullmatch(bruto) else bruto
