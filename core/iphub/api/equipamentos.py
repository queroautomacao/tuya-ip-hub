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
    CATALOGO,
    GESTOR,
    VARREDURA,
    com_sessao,
    config_de,
    drivers_de,
    ler_corpo,
    licencas_de,
    resposta_ok,
    trocar_config,
)
from iphub.api.formato import achado_json, equipamento_json, manifesto_json
from iphub.config import LISTAS, LISTAS_MAXIMO, Cadastro, Item, ip_literal, item_valido
from iphub.dpbus import mapa, perfil, protocolo
from iphub.dpbus import numeros as modulo_numeros
from iphub.drivers import descoberta
from iphub.drivers.gestor import ErroDeCadastro, Gestor
from iphub.drivers.manifesto import Manifesto, TipoCampo, produto_de
from iphub.drivers.manifesto import por_lista as sugestoes_por_lista
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.equipamentos")

# Why: the sweep waits for the segment to answer, and the hard limit is what keeps a socket
# that never returns from holding the route open until the panel gives up.
# Por que: a varredura espera o segmento responder, e o limite duro é o que impede um socket
# que nunca volta de segurar a rota até o painel desistir.
TIMEOUT_VARREDURA_S = 3.0
LIMITE_VARREDURA_S = 8.0
DESTINO = descoberta.DESTINO_PADRAO
DESTINO_MDNS = descoberta.DESTINO_MDNS

TEXTO_MAXIMO = 200

# The actions of section 14 that a group routes to the master, which the book of licences
# does; everything else goes straight to the equipment the way it always did.
# As ações da seção 14 que um grupo roteia para o mestre, o que o livro de licenças faz; todo
# o resto vai direto ao equipamento como sempre foi.
ACOES_ROTEADAS = ("volume", *modulo_numeros.DO_MESTRE)

CORPO_INVALIDO = "corpo_invalido"
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
# O status de todo código estável que estas rotas respondem; nada mais chega ao painel.
STATUS_POR_CODIGO = {
    CAMPO_INVALIDO: 400,
    CORPO_INVALIDO: 400,
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
    cadastro = Cadastro(
        identidade=_identidade_do_corpo(dados, identidade),
        tipo=manifesto.tipo,
        nome=_texto(dados.get("nome", "")).strip(),
        ip=endereco,
        campos=campos,
        segredos=segredos,
        listas=_listas(dados.get("listas"), anterior, manifesto),
    )
    # Why: section 8, the profile of an equipment fits 200 bytes on any number, and what does
    # not fit is refused where it is typed instead of leaving the panel of the platform blank.
    # Por que: seção 8, o perfil de um equipamento cabe em 200 bytes em qualquer número, e o
    # que não cabe é recusado onde é digitado em vez de deixar o painel da plataforma em branco.
    if not perfil.cabe_em_qualquer_numero(cadastro, manifesto):
        raise _Recusa(PERFIL_LONGO)
    return cadastro


def _listas(
    brutas: object, anterior: Cadastro | None, manifesto: Manifesto
) -> dict[str, tuple[Item, ...]]:
    """The lists of section 8 as the body sent them; the stored ones on an update that sent
    none, and what the driver suggests on a registration that sent none.

    Why: the value of a shortcut is a string of the protocol of the device, so an equipment
    that arrives with an empty list leaves the integrator guessing what to type. A driver that
    suggests items hands over three that work, and clearing them stays possible because an
    update that sends an empty object is a body that sent lists.

    As listas da seção 8 como o corpo as mandou; as guardadas numa atualização que não mandou
    nenhuma, e o que o driver sugere num cadastro que não mandou nenhuma.

    Por que: o valor de um atalho é uma string do protocolo do aparelho, então um equipamento
    que chega com lista vazia deixa o integrador adivinhando o que escrever. Um driver que
    sugere itens entrega três que funcionam, e apagá-los continua possível porque uma
    atualização que manda um objeto vazio é um corpo que mandou listas.
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


def _persistir(
    app: web.Application,
    cadastros: tuple[Cadastro, ...],
    numeros: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    # Why: the route writes the set (section 6) and answers whether it could, because a gestor
    # changed first left the daemon polling an equipment that never reached the disk, listed by
    # the panel until a restart made it vanish.
    # Por que: a rota grava o conjunto (seção 6) e responde se conseguiu, porque um gestor
    # alterado primeiro deixava o daemon com um equipamento que nunca chegou ao disco, listado
    # pelo painel até um reinício o sumir.
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
    # Why: section 8, the profiles of a licence share five strings, so an edited registration
    # that would push the licence past them is refused now, with the integrator at the
    # keyboard, instead of taking every profile of the licence off the bus.
    # Por que: seção 8, os perfis de uma licença dividem cinco strings, então um cadastro
    # editado que empurraria a licença para além delas é recusado agora, com o integrador no
    # teclado, em vez de tirar todo perfil da licença do barramento.
    if not licencas_de(app).perfis_cabem(cadastro):
        return _erro(mapa.PERFIS_LONGOS)
    # Why: section 8, an equipment only enters a licence of its product, so a change of tipo
    # that would move it to the other product while it holds a number is refused now instead
    # of emptying the number in silence on the next boot.
    # Por que: seção 8, um equipamento só entra numa licença do produto dele, então uma troca
    # de tipo que o levaria ao outro produto enquanto ele ocupa um número é recusada agora em
    # vez de esvaziar o número em silêncio no próximo boot.
    onde = licencas_de(app).onde(cadastro.identidade)
    manifesto = _manifestos(app).get(cadastro.tipo)
    if onde is not None and manifesto is not None:
        if produto_de(manifesto.categoria) != licencas_de(app).produto_de(onde[0]):
            return _erro(modulo_numeros.PRODUTO_INCOMPATIVEL)
    # Why: section 6, any registered equipment may occupy a number, so a change of tipo keeps
    # the number: the data points follow the new manifest on the next report.
    # Por que: seção 6, qualquer equipamento cadastrado pode ocupar um número, então uma troca
    # de tipo mantém o número: os data points seguem o manifesto novo no próximo report.
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
    # Why: section 8 numbers by position, so the number of a removed equipment stays there,
    # empty; closing the hole would move every equipment below it one number up, in silence,
    # on a bus the customer already automated. The group it was in falls with it, because a
    # group led by an equipment nobody has is a group nobody can take down.
    # Por que: a seção 8 numera pela posição, então o número de um equipamento removido
    # continua ali, vazio; fechar o buraco moveria todo equipamento abaixo dele um número para
    # cima, em silêncio, num barramento que o cliente já automatizou. O grupo em que ele
    # estava cai junto, porque um grupo liderado por um equipamento que ninguém tem é um grupo
    # que ninguém consegue desfazer.
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
    # Why: section 14, the volume, the transport and the radios of a speaker that follows a
    # master go to the master, and the book of licences is what knows who leads whom; a press
    # on the detail screen takes the same road a scene step and the bus take, so it never lands
    # on a slave that would refuse it or break the group. Only a slave takes that road: the
    # book serializes it behind every set of the licence, and a solo equipment or the master
    # answers for itself as fast as it always did.
    # Por que: seção 14, o volume, o transporte e as rádios de uma caixa que segue um mestre
    # vão para o mestre, e o livro de licenças é quem sabe quem lidera quem; uma apertada na
    # tela de detalhe toma o mesmo caminho que um passo de cena e o barramento tomam, então
    # nunca cai num escravo que a recusaria ou quebraria o grupo. Só um escravo toma esse
    # caminho: o livro o serializa atrás de todo set da licença, e um equipamento solo ou o
    # mestre responde por si tão rápido quanto sempre respondeu.
    if nome in ACOES_ROTEADAS and livro.segue_um_mestre(identidade):
        codigo = await livro.acionar(identidade, nome, valor)
        return resposta_ok() if codigo is None else _erro(_do_barramento(codigo))
    codigo = await _gestor(request).executar(identidade, nome, valor)
    return resposta_ok() if codigo is None else _erro(codigo)


def _do_barramento(codigo: str) -> str:
    """The code of the bus in the vocabulary of this route, so a refusal through the master
    answers the same status the direct road answers.

    O código do barramento no vocabulário desta rota, para uma recusa pelo mestre responder o
    mesmo status que o caminho direto responde.
    """
    if codigo == protocolo.NUMERO_OFFLINE:
        return "eq_offline"
    if codigo == protocolo.VALOR_INVALIDO:
        return INVALID_VALUE
    return codigo


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
    except descoberta.PlanoAmbiguo as erro:
        log.warning("discovery sweep failed: %s", erro)
        return ERRO_INTERNO
    try:
        async with asyncio.timeout(LIMITE_VARREDURA_S):
            # Why: section 6 generates the discovery from the manifests, and a manifest
            # declares its signature on the transport its device answers: the multiroom
            # speaker of section 14 is only ever found by mDNS, and sweeping SSDP alone made
            # the panel answer "nothing here" on a segment full of speakers. The two run
            # together because the sweep floods the segment either way and its length is the
            # slower of the two, not their sum.
            # Por que: a seção 6 gera a descoberta dos manifestos, e um manifesto declara sua
            # assinatura no transporte que o aparelho dele responde: a caixa multiroom da
            # seção 14 só é achada por mDNS, e varrer só SSDP fazia o painel responder "não há
            # nada aqui" num segmento cheio de caixas. Os dois correm juntos porque a
            # varredura inunda o segmento de todo jeito e a duração dela é a do mais lento dos
            # dois, não a soma.
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


# Why: the mDNS answer of a speaker carries its name and its address and not its uuid, and
# section 6 registers the uuid; without this the sweep found the speaker and the panel still
# asked the operator to type its identity by hand.
# Por que: a resposta mDNS de uma caixa leva o nome e o endereço e não o uuid, e a seção 6
# cadastra o uuid; sem isto a varredura achava a caixa e o painel ainda pedia ao operador
# que digitasse a identidade na mão.
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

    Why: one transport failing is a fault of this host on that transport, and throwing away
    what the other one found would hide the speakers the panel is there to show. Both failing
    is the fault section 9 wants reported, because answering an empty list would send the
    integrator hunting the network instead of the daemon.

    Os achados dos dois transportes, com um aparelho visto nos dois contado uma vez.

    Por que: um transporte falhando é falha deste host naquele transporte, e jogar fora o que
    o outro achou esconderia as caixas que o painel existe para mostrar. Os dois falharem é a
    falha que a seção 9 quer reportada, porque responder lista vazia mandaria o integrador
    caçar a rede em vez do daemon.
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
        # Why: the same speaker answering both transports is one device, and section 6 says
        # the identity decides; only an answer with no identity falls back to the address.
        # Por que: a mesma caixa respondendo os dois transportes é um aparelho só, e a seção 6
        # diz que a identidade decide; só uma resposta sem identidade recai no endereço.
        chave = ("id", achado.identidade) if achado.identidade else ("ip", achado.ip)
        vistos.setdefault(chave, achado)
    return tuple(vistos.values())
