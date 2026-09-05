# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 over HTTP: the licences, the numbers of each, the data points and the group.

A licence is a device on the platform (uuid, pid and chave) and a slice of the bus; a number
is what section 8 says it is, one of the equipment numbers of a licence that any registered
equipment of the right product may occupy, so these routes carry no second registry: the
order is a list of identities already registered as equipment, and the book of licences is
the one that judges it and answers a stable code. Nothing here decides where a set goes
either; aplicar_dp of the common module does, so the panel and the bus of the same hub
cannot disagree about it.

Section 9: the chave of a licence is the credential of the device on the platform, so it is
written here and never read back; the answers carry only whether one is defined, and the QR
code carries the uuid and the pid, which is what the app needs to pair.

The numbers of section 8 leave the daemon in the answers instead of being written a second
time in the panel: each number carries the data point of each of its functions, and the
data points route carries the whole table of the product. A panel that computed the
numbering would be a second copy of the contract, and the day the contract moved only one of
them would move.

Seção 8 sobre HTTP: as licenças, os números de cada uma, os data points e o grupo.

Uma licença é um dispositivo na plataforma (uuid, pid e chave) e uma fatia do barramento; um
número é o que a seção 8 diz que ele é, um dos números de equipamento de uma licença que
qualquer equipamento cadastrado do produto certo pode ocupar, então estas rotas não carregam
segundo cadastro: a ordem é uma lista de identidades já cadastradas como equipamento, e o
livro de licenças é quem a julga e responde um código estável. Nada aqui decide para onde
vai um set tampouco; quem decide é o aplicar_dp do módulo comum, para o painel e o barramento
do mesmo hub não poderem discordar disso.

Seção 9: a chave de uma licença é a credencial do dispositivo na plataforma, então ela é
escrita aqui e nunca lida de volta; as respostas levam só se há uma definida, e o QR code
leva o uuid e o pid, que é o que o app precisa para parear.

Os números da seção 8 saem do daemon nas respostas em vez de serem escritos uma segunda vez
no painel: cada número carrega o data point de cada função dele, e a rota de data points
carrega a tabela inteira do produto. Um painel que calculasse a numeração seria uma segunda
cópia do contrato, e no dia em que o contrato mudasse só uma das duas mudaria.
"""

import logging
import re
from dataclasses import replace
from urllib.parse import quote

from aiohttp import web

from iphub import cenas
from iphub.api.comum import (
    GESTOR,
    aplicar_dp,
    com_sessao,
    config_de,
    ler_corpo,
    licencas_de,
    resposta_ok,
    trocar_config,
    valores_dps,
)
from iphub.api.formato import estado_json
from iphub.config import ID_DE_LICENCA, Cadastro, Licenca
from iphub.dpbus import mapa, protocolo
from iphub.dpbus import numeros as modulo
from iphub.dpbus.socket import BARRAMENTO
from iphub.drivers.manifesto import Estado
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.licencas")

_SO_DIGITOS = re.compile(r"[0-9]{1,10}")

CORPO_INVALIDO = "corpo_invalido"
ERRO_INTERNO = "erro_interno"
LICENCA_INVALIDA = "licenca_invalida"
LICENCA_REPETIDA = "licenca_repetida"
LICENCA_NAO_ENCONTRADA = "licenca_nao_encontrada"
LICENCA_INCOMPLETA = "licenca_incompleta"
LICENCAS_DEMAIS = "licencas_demais"
PRODUTO_INVALIDO = "produto_invalido"

# Why: every licence is a device the bridge serves and a slice the bus walks every second, so
# the book has a ceiling a home never reaches and a session holder cannot fill config.json.
# Por que: toda licença é um dispositivo que a ponte serve e uma fatia que o barramento
# percorre a cada segundo, então o livro tem um teto que uma casa nunca alcança e quem tem
# sessão não consegue encher o config.json.
LICENCAS_MAXIMO = 16

PAPEL_MESTRE = "mestre"
PAPEL_ESCRAVO = "escravo"
# Why: a speaker held in a group this hub does not lead refuses volume, transport, radios and
# input, and nothing here routes them anywhere; the panel has to say so instead of promising
# the master it would promise for a slave of the hub's own group.
# Por que: uma caixa presa num grupo que este hub não lidera recusa volume, transporte, rádios
# e entrada, e nada aqui os roteia para lugar nenhum; o painel tem de dizer isso em vez de
# prometer o mestre que prometeria para um escravo do grupo do próprio hub.
PAPEL_ALHEIO = "alheio"
PAPEL_SOLO = ""

NOME_MAXIMO = 40
# Why: the uuid and the pid of a device on the platform are short ASCII identifiers, and the
# authkey is 32 hexadecimal characters; a ceiling keeps config.json from holding a paragraph.
# Por que: o uuid e o pid de um dispositivo na plataforma são identificadores ASCII curtos, e
# a authkey são 32 caracteres hexadecimais; um teto impede o config.json de guardar um
# parágrafo.
IDENTIFICADOR_MAXIMO = 64
CHAVE_MAXIMA = 128

# The pairing QR code of a device of the platform: the pid and the uuid, in the URL the app
# of the platform scans. One place to change if the platform moves it.
# O QR code de pareamento de um dispositivo da plataforma: o pid e o uuid, na URL que o app
# da plataforma escaneia. Um lugar só para mudar se a plataforma o mover.
QR_MODELO = "https://smartapp.tuya.com/s/p?p={pid}&uuid={uuid}&v=2.0"

# The status of every stable code these routes answer; nothing else reaches the panel.
# O status de todo código estável que estas rotas respondem; nada mais chega ao painel.
STATUS_POR_CODIGO = {
    CORPO_INVALIDO: 400,
    LICENCA_INVALIDA: 400,
    PRODUTO_INVALIDO: 400,
    LICENCA_REPETIDA: 409,
    LICENCA_NAO_ENCONTRADA: 404,
    LICENCA_INCOMPLETA: 409,
    LICENCAS_DEMAIS: 409,
    modulo.NUMEROS_DEMAIS: 400,
    modulo.NUMERO_REPETIDO: 400,
    modulo.NUMERO_OCUPADO: 409,
    modulo.IDENTIDADE_INVALIDA: 400,
    modulo.PRODUTO_INCOMPATIVEL: 400,
    mapa.PERFIS_LONGOS: 400,
    "eq_nao_encontrado": 404,
    protocolo.LICENCA_DESCONHECIDA: 404,
    protocolo.DP_DESCONHECIDO: 404,
    protocolo.DP_SOMENTE_LEITURA: 400,
    protocolo.VALOR_INVALIDO: 400,
    protocolo.NUMERO_OFFLINE: 503,
    "nao_suportado": 400,
    "auth_pendente": 409,
    "erro_aparelho": 502,
    # Why: the scene data point runs a scene, so the two codes the scene executor answers
    # reach these routes too, and without them the panel got a 500 with erro_interno for a
    # scene that simply does not exist or is already running, while the scenes route answered
    # 404 and 409 for the very same conditions.
    # Por que: o data point de cena executa uma cena, então os dois códigos que o executor de
    # cenas responde chegam também a estas rotas, e sem eles o painel recebia um 500 com
    # erro_interno para uma cena que simplesmente não existe ou já está rodando, enquanto a
    # rota de cenas respondia 404 e 409 para as mesmíssimas condições.
    cenas.CENA_NAO_ENCONTRADA: 404,
    cenas.CENA_EM_CURSO: 409,
    ERRO_INTERNO: 500,
}


class _Recusa(Exception):
    """A stable code on the way out of the validation, so no handler builds a status.

    Um código estável na saída da validação, para nenhum handler montar um status.
    """

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def erro(codigo: str) -> web.Response:
    """The stable code as the status the panel reads, and never a status invented here.

    O código estável como o status que o painel lê, e nunca um status inventado aqui.
    """
    return resposta_erro(STATUS_POR_CODIGO.get(codigo, 500), codigo)


def _cadastros(request: web.Request) -> dict[str, Cadastro]:
    return {cadastro.identidade: cadastro for cadastro in request.app[GESTOR].cadastros}


def _id(request: web.Request) -> str:
    return request.match_info["id"]


def _papel(numero: int, mestre: int, escravos: tuple[int, ...], alheios: tuple[int, ...]) -> str:
    """What the speaker of this number really is right now, and not only what the hub asked.

    Why: a speaker the customer grouped with the app of the manufacturer is a slave of a group
    this hub does not lead, and it refuses volume, transport, preset and input. Calling that
    solo drew a panel full of controls that only ever answer no, with nothing saying why.

    O que a caixa deste número realmente é agora, e não só o que o hub pediu.

    Por que: uma caixa que o cliente agrupou com o app do fabricante é escrava de um grupo que
    este hub não lidera, e ela recusa volume, transporte, preset e entrada. Chamar isso de solo
    desenhava um painel cheio de controles que só respondem não, sem nada dizendo por quê.
    """
    if mestre and numero == mestre:
        return PAPEL_MESTRE
    if numero in escravos:
        return PAPEL_ESCRAVO
    if numero in alheios:
        return PAPEL_ALHEIO
    return PAPEL_SOLO


def _dps_do_numero(produto: str, numero: int) -> dict[str, int]:
    """The data point of every function of one number, straight from the map of section 8.

    O data point de cada função de um número, direto do mapa da seção 8.
    """
    return {dp.funcao: dp.dpid for dp in mapa.tabela(produto) if dp.numero == numero}


def _numero_json(
    produto: str,
    numero: int,
    cadastro: Cadastro | None,
    estado: Estado | None,
    papel: str,
) -> dict:
    """One number as the panel reads it: who occupies it, what it says and its data points.

    Um número como o painel o lê: quem o ocupa, o que ele diz e os data points dele.
    """
    # Why: a number nobody occupies is a normal state of the hub (section 6 works with zero
    # equipment), so it answers with an empty identity and a null state instead of vanishing
    # from the list; the POSITION is the contract, and a shorter list would move it.
    # Por que: um número que ninguém ocupa é estado normal do hub (a seção 6 funciona com zero
    # equipamento), então ele responde com identidade vazia e estado nulo em vez de sumir da
    # lista; a POSIÇÃO é o contrato, e uma lista mais curta a moveria.
    return {
        "numero": numero,
        "identidade": "" if cadastro is None else cadastro.identidade,
        "nome": "" if cadastro is None else cadastro.nome,
        "tipo": "" if cadastro is None else cadastro.tipo,
        "papel": papel,
        "dps": _dps_do_numero(produto, numero),
        "estado": None if estado is None else estado_json(estado),
    }


def _licenca_json(request: web.Request, numeros: modulo.Numeros) -> dict:
    """One licence as the panel reads it: never the chave, only whether one is defined.

    Uma licença como o painel a lê: nunca a chave, só se há uma definida.
    """
    licenca = numeros.licenca
    cadastros = _cadastros(request)
    estados = request.app[GESTOR].estados()
    mestre = numeros.grupo()
    escravos = numeros.escravos()
    alheios = numeros.escravos_alheios() if numeros.multiroom else ()
    lista = []
    for numero in range(1, numeros.capacidade + 1):
        identidade = numeros.identidade(numero)
        lista.append(
            _numero_json(
                numeros.produto,
                numero,
                cadastros.get(identidade),
                estados.get(identidade),
                _papel(numero, mestre, escravos, alheios) if numeros.multiroom else PAPEL_SOLO,
            )
        )
    return {
        "id": licenca.id,
        "produto": licenca.produto,
        "nome": licenca.nome,
        "uuid": licenca.uuid,
        "pid": licenca.pid,
        "chave_definida": bool(licenca.chave),
        "capacidade": numeros.capacidade,
        "numeros": lista,
        "grupo": mestre,
        "reports_do_dia": request.app[BARRAMENTO].reports_do_dia(licenca.id),
        "ouvintes": request.app[BARRAMENTO].ouvintes_de(licenca.id),
    }


def _texto(bruto: object, maximo: int, *, ascii_apenas: bool = False) -> str:
    """A printable string within the ceiling, or licenca_invalida; the caller never guesses.

    Um texto imprimível dentro do teto, ou licenca_invalida; quem chama nunca adivinha.
    """
    if not isinstance(bruto, str) or len(bruto) > maximo or not bruto.isprintable():
        raise _Recusa(LICENCA_INVALIDA)
    if ascii_apenas and not bruto.isascii():
        raise _Recusa(LICENCA_INVALIDA)
    return bruto.strip()


def _montar_licenca(dados: dict, anterior: Licenca | None, existentes: tuple[str, ...]) -> Licenca:
    """The body as a Licenca, or a _Recusa naming what refused it.

    O corpo como Licenca, ou uma _Recusa nomeando o que a recusou.
    """
    if anterior is None:
        produto = dados.get("produto")
        if produto not in mapa.PRODUTOS:
            raise _Recusa(PRODUTO_INVALIDO)
        id_licenca = _id_nova(dados.get("id"), produto, existentes)
    else:
        # Why: the product decides the table of section 8 and the numbers already assigned,
        # so it never changes on an edit; a licence of the other product is another licence.
        # Por que: o produto decide a tabela da seção 8 e os números já atribuídos, então ele
        # nunca muda numa edição; uma licença do outro produto é outra licença.
        if "produto" in dados and dados["produto"] != anterior.produto:
            raise _Recusa(PRODUTO_INVALIDO)
        produto = anterior.produto
        id_licenca = anterior.id
    guardada = anterior or Licenca(id=id_licenca, produto=produto)
    return Licenca(
        id=id_licenca,
        produto=produto,
        nome=_texto(dados["nome"], NOME_MAXIMO) if "nome" in dados else guardada.nome,
        uuid=_campo(dados, "uuid", guardada.uuid, IDENTIFICADOR_MAXIMO),
        pid=_campo(dados, "pid", guardada.pid, IDENTIFICADOR_MAXIMO),
        # Why: an absent chave keeps the stored one, because an edit that only fixes the name
        # must not erase the credential of the device; an empty string erases it on purpose.
        # Por que: uma chave ausente mantém a guardada, porque uma edição que só conserta o
        # nome não pode apagar a credencial do dispositivo; a string vazia apaga de propósito.
        chave=_campo(dados, "chave", guardada.chave, CHAVE_MAXIMA),
    )


def _campo(dados: dict, chave: str, guardado: str, maximo: int) -> str:
    if chave not in dados:
        return guardado
    return _texto(dados[chave], maximo, ascii_apenas=True)


def _id_nova(bruto: object, produto: str, existentes: tuple[str, ...]) -> str:
    """The id the body named, or the first free one of that product.

    O id que o corpo nomeou, ou o primeiro livre daquele produto.
    """
    if bruto is None or bruto == "":
        contador = 1
        while f"{produto}{contador}" in existentes:
            contador += 1
        return f"{produto}{contador}"
    if not isinstance(bruto, str) or not ID_DE_LICENCA.fullmatch(bruto):
        raise _Recusa(LICENCA_INVALIDA)
    if bruto in existentes:
        raise _Recusa(LICENCA_REPETIDA)
    return bruto


def _persistir(app: web.Application, **mudanca: object) -> bool:
    try:
        trocar_config(app, replace(config_de(app), **mudanca))
    except OSError as falha:
        log.error("could not write the licences: %s", falha)
        return False
    return True


def _com(licencas: tuple[Licenca, ...], licenca: Licenca) -> tuple[Licenca, ...]:
    if all(atual.id != licenca.id for atual in licencas):
        return (*licencas, licenca)
    return tuple(licenca if atual.id == licenca.id else atual for atual in licencas)


@com_sessao
async def listar(request: web.Request) -> web.Response:
    livro = licencas_de(request.app)
    return resposta_ok(
        licencas=[_licenca_json(request, numeros) for numeros in livro.todas()],
        produtos={produto: mapa.NUMEROS[produto] for produto in mapa.PRODUTOS},
        reports_por_dia=mapa.REPORTS_POR_DIA,
        aviso_do_dia=mapa.AVISO_DO_DIA,
    )


@com_sessao
async def criar(request: web.Request) -> web.Response:
    return await _gravar(request, None)


@com_sessao
async def atualizar(request: web.Request) -> web.Response:
    """A field the body omits keeps the stored value, the chave included; an empty string
    erases it.

    Um campo que o corpo omite mantém o valor guardado, a chave inclusive; um vazio o apaga.
    """
    return await _gravar(request, _id(request))


async def _gravar(request: web.Request, id_licenca: str | None) -> web.Response:
    app = request.app
    livro = licencas_de(app)
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    anterior = None
    if id_licenca is not None:
        numeros = livro.de(id_licenca)
        if numeros is None:
            return erro(LICENCA_NAO_ENCONTRADA)
        anterior = numeros.licenca
    elif len(livro.ids()) >= LICENCAS_MAXIMO:
        return erro(LICENCAS_DEMAIS)
    try:
        licenca = _montar_licenca(dados, anterior, livro.ids())
    except _Recusa as recusa:
        return erro(recusa.codigo)
    # Why: the file is written before the book takes the licence, because a licence that
    # lived only in memory would vanish on the next boot while the device on the platform
    # stayed paired to a hub that no longer knows it.
    # Por que: o arquivo é gravado antes de o livro assumir a licença, porque uma licença que
    # vivesse só na memória sumiria no próximo boot enquanto o dispositivo na plataforma
    # seguiria pareado a um hub que não a conhece mais.
    if not _persistir(app, licencas=_com(config_de(app).licencas, licenca)):
        return erro(ERRO_INTERNO)
    if anterior is None:
        livro.adicionar(licenca)
    else:
        livro.trocar(licenca)
    return resposta_ok(licenca=_licenca_json(request, livro.de(licenca.id)))


@com_sessao
async def remover(request: web.Request) -> web.Response:
    """The licence leaves with its numbers; the equipment stays registered, section 9.

    A licença sai com os números dela; o equipamento continua cadastrado, seção 9.
    """
    app = request.app
    livro = licencas_de(app)
    id_licenca = _id(request)
    if livro.de(id_licenca) is None:
        return erro(LICENCA_NAO_ENCONTRADA)
    cfg = config_de(app)
    restantes = tuple(licenca for licenca in cfg.licencas if licenca.id != id_licenca)
    numeros = {chave: ordem for chave, ordem in livro.numeros().items() if chave != id_licenca}
    if not _persistir(app, licencas=restantes, numeros=numeros):
        return erro(ERRO_INTERNO)
    await livro.remover(id_licenca)
    await app[BARRAMENTO].desligar(id_licenca)
    return resposta_ok()


@com_sessao
async def definir_numeros(request: web.Request) -> web.Response:
    """Saves the order of the numbers of one licence, which is the only registry a number
    ever has.

    Grava a ordem dos números de uma licença, que é o único cadastro que um número tem.
    """
    app = request.app
    livro = licencas_de(app)
    id_licenca = _id(request)
    if livro.de(id_licenca) is None:
        return erro(LICENCA_NAO_ENCONTRADA)
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    try:
        ordem = livro.validar_ordem(id_licenca, dados.get("numeros"))
    except modulo.OrdemInvalida as recusa:
        return erro(recusa.codigo)
    # Why: the file is written before the order is applied, because an order that lived only
    # in memory would move an equipment back to its old number on the next boot, in silence,
    # on a bus the customer already automated.
    # Por que: o arquivo é gravado antes de a ordem valer, porque uma ordem que vivesse só na
    # memória moveria um equipamento de volta ao número antigo no próximo boot, em silêncio,
    # num barramento que o cliente já automatizou.
    if not _persistir(app, numeros={**livro.numeros(), id_licenca: ordem}):
        return erro(ERRO_INTERNO)
    try:
        # Why: the book judges the order again under its own lock, so an equipment removed
        # between the two calls is a stable code and never a 500 with a traceback.
        # Por que: o livro julga a ordem de novo sob a trava dele, então um equipamento
        # removido entre as duas chamadas é um código estável e nunca um 500 com traceback.
        await livro.definir_ordem(id_licenca, ordem)
    except modulo.OrdemInvalida as recusa:
        return erro(recusa.codigo)
    return resposta_ok(numeros=list(ordem))


@com_sessao
async def dps(request: web.Request) -> web.Response:
    """Every reportable data point of one licence and the table of its product, for the
    panel and for curl.

    Todo data point reportável de uma licença e a tabela do produto dela, para o painel e
    para o curl.
    """
    app = request.app
    id_licenca = _id(request)
    produto = licencas_de(app).produto_de(id_licenca)
    if produto is None:
        return erro(LICENCA_NAO_ENCONTRADA)
    quadro = protocolo.snapshot(produto, valores_dps(app, id_licenca))
    tabela = [
        {
            "dpid": dp.dpid,
            "numero": dp.numero,
            "indice": dp.indice,
            "funcao": dp.funcao,
            "tipo": dp.tipo.value,
            "sentido": dp.sentido.value,
            "classe": dp.classe.value,
            "valores": list(dp.valores),
            "minimo": dp.minimo,
            "maximo": dp.maximo,
            "empurrado": dp.empurrado,
        }
        for dp in mapa.tabela(produto)
    ]
    return resposta_ok(
        dps=quadro["dps"],
        mapa=tabela,
        produto=produto,
        reports_do_dia=app[BARRAMENTO].reports_do_dia(id_licenca),
    )


@com_sessao
async def ajustar(request: web.Request) -> web.Response:
    """One set of section 8 by HTTP, with the same effect a set on the bus would have.

    Um set da seção 8 por HTTP, com o mesmo efeito que um set no barramento teria.
    """
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    codigo = await request.app[BARRAMENTO].aplicar(_id(request), _dpid(request), dados.get("v"))
    return resposta_ok() if codigo is None else erro(codigo)


@com_sessao
async def grupo(request: web.Request) -> web.Response:
    """The group of the licence: 0 takes it down, n makes number n lead, and membros names
    exactly who follows.

    Why: section 14, a master carries up to seven slaves and the customer picks them one by
    one, so the panel sends the set it wants. A body without membros keeps the meaning the
    data point of the bus has, which is every speaker of the tipo of the master.

    O grupo da licença: 0 o derruba, n faz o número n liderar, e membros nomeia exatamente
    quem segue.

    Por que: seção 14, um mestre leva até sete escravos e o cliente os escolhe um a um, então
    o painel manda o conjunto que quer. Um corpo sem membros mantém o sentido que o data point
    do barramento tem, que é toda caixa do tipo do mestre.
    """
    id_licenca = _id(request)
    livro = licencas_de(request.app)
    numeros = livro.de(id_licenca)
    if numeros is None:
        return erro(LICENCA_NAO_ENCONTRADA)
    if not numeros.multiroom:
        return erro("nao_suportado")
    dados = await ler_corpo(request)
    if dados is None:
        return erro(CORPO_INVALIDO)
    log.debug(
        "painel: grupo de %s = %r, membros %r", id_licenca, dados.get("v"), dados.get("membros")
    )
    membros = _membros(dados.get("membros"), numeros.capacidade)
    if membros is _INVALIDO:
        return erro(protocolo.VALOR_INVALIDO)
    codigo = await livro.formar(id_licenca, dados.get("v"), membros)
    if codigo is not None:
        return erro(codigo)
    return resposta_ok(grupo=numeros.grupo(), membros=list(numeros.escravos()))


# The answer of a body that named members outside the contract, which is not an empty choice.
# A resposta de um corpo que nomeou membros fora do contrato, que não é escolha vazia.
_INVALIDO: list[int] = []


def _membros(bruto: object, capacidade: int) -> list[int] | None:
    """The chosen members as numbers of this licence, None when the body chose nothing.

    Os membros escolhidos como números desta licença, None quando o corpo não escolheu nada.
    """
    if bruto is None:
        return None
    if not isinstance(bruto, list) or len(bruto) > capacidade:
        return _INVALIDO
    escolhidos = []
    for numero in bruto:
        if type(numero) is not int or not 1 <= numero <= capacidade:
            return _INVALIDO
        escolhidos.append(numero)
    return escolhidos


@com_sessao
async def qr(request: web.Request) -> web.Response:
    """What the QR code of the licence carries: the pid and the uuid, never the chave.

    O que o QR code da licença leva: o pid e o uuid, nunca a chave.
    """
    numeros = licencas_de(request.app).de(_id(request))
    if numeros is None:
        return erro(LICENCA_NAO_ENCONTRADA)
    licenca = numeros.licenca
    if not licenca.uuid or not licenca.pid:
        return erro(LICENCA_INCOMPLETA)
    conteudo = QR_MODELO.format(pid=quote(licenca.pid, safe=""), uuid=quote(licenca.uuid, safe=""))
    return resposta_ok(conteudo=conteudo, uuid=licenca.uuid, pid=licenca.pid)


def _dpid(request: web.Request) -> object:
    """The number in the path, or the raw text when it is not a number at all.

    O número no caminho, ou o texto cru quando ele não é número nenhum.
    """
    # Why: the map answers dp_desconhecido for anything that is not an int, so a path of
    # letters travels as text and is refused by the same rule that refuses dp 999. Only an
    # ASCII digit is a number here, because str.isdigit() is true for characters int()
    # refuses, and the ceiling keeps a path of a thousand digits from becoming a thousand
    # digit int.
    # Por que: o mapa responde dp_desconhecido para o que não for int, então um caminho de
    # letras viaja como texto e é recusado pela mesma regra que recusa o dp 999. Só dígito
    # ASCII é número aqui, porque str.isdigit() é verdadeiro para caracteres que o int()
    # recusa, e o teto impede que um caminho de mil dígitos vire um int de mil dígitos.
    bruto = request.match_info["dpid"]
    return int(bruto) if _SO_DIGITOS.fullmatch(bruto) else bruto


__all__ = ["aplicar_dp", "erro"]
