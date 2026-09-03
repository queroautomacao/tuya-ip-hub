# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7 over HTTP: the declarative drivers the hub carries, and the editor of the panel.

The daemon is the authority that accepts a driver file. It answers every problem of a file
at once, as (campo, codigo) pairs the panel translates, so a refusal is fixed in one pass
instead of one round trip per mistake. Nothing here interprets the format: the validation of
the declarativo package decides, the loader decides what enters the catalog, and these
routes only turn what they answer into a status.

Two rules of section 9 shape the writing. The file the panel saves is named after the tipo
the validation accepted, which is an identifier of [a-z0-9_], and the file a delete removes
is the one the LOADER found, never a name built from the path of the request, so no request
addresses a file outside the drivers directory. And the file is written the way every other
file of /data is: atomic and 0600.

Seção 7 sobre HTTP: os drivers declarativos que o hub carrega, e o editor do painel.

O daemon é a autoridade que aceita um arquivo de driver. Ele responde todo problema de um
arquivo de uma vez, como pares (campo, codigo) que o painel traduz, para uma recusa ser
consertada numa passada em vez de uma ida e volta por erro. Nada aqui interpreta o formato:
a validação do pacote declarativo decide, o carregador decide o que entra no catálogo, e
estas rotas só transformam o que eles respondem num status.

Duas regras da seção 9 moldam a escrita. O arquivo que o painel salva tem o nome do tipo que
a validação aceitou, que é um identificador de [a-z0-9_], e o arquivo que uma remoção apaga é
o que o CARREGADOR achou, nunca um nome montado a partir do caminho da requisição, então
nenhuma requisição endereça arquivo fora da pasta de drivers. E o arquivo é gravado como todo
outro arquivo do /data: de forma atômica e 0600.
"""

import asyncio
import functools
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

from iphub import regex_seguro
from iphub.api.comum import (
    GESTOR,
    TRAVA_DRIVERS,
    catalogo_de,
    com_sessao,
    ler_corpo,
    resposta_ok,
)
from iphub.api.formato import manifesto_json
from iphub.arquivos import escrever_texto, garantir_diretorio, ler_texto
from iphub.drivers.catalogo import (
    ARQUIVO_MAXIMO,
    CAMPO_TIPO,
    DECL_ARQUIVO_GRANDE,
    DECL_INVALIDO,
    DECL_TIPO_OCUPADO,
    ORIGEM_INTEGRADOR,
    Catalogo,
    Declarativo,
)
from iphub.drivers.declarativo.formato import CAMPO_ARQUIVO, DeclaracaoInvalida, Definicao
from iphub.drivers.declarativo.formato import validar as validar_declaracao
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.declarativos")

# Why: the body carries one driver file plus the object that wraps it, and the loader refuses
# a file past ARQUIVO_MAXIMO, so reading more than that would only be reading to throw away.
# Por que: o corpo leva um arquivo de driver mais o objeto que o embrulha, e o carregador
# recusa arquivo acima de ARQUIVO_MAXIMO, então ler mais que isso seria ler para jogar fora.
CORPO_MAXIMO_DRIVER = ARQUIVO_MAXIMO + 1024

# Why: judging a file is another process and a deadline per pattern, and asyncio.to_thread
# spends a thread of the DEFAULT pool, which is the same pool every declarative read goes
# through (regex_seguro.buscar_async reads through it). A panel validating a hostile file
# would take one thread of it per request and stall the poll of every device. One thread of
# our own is the whole bound: two integrators saving at the same time queue behind each other
# instead of doubling the work, and no read ever waits behind them.
# Por que: julgar um arquivo é outro processo e um prazo por padrão, e o asyncio.to_thread
# gasta uma thread da piscina PADRÃO, que é a mesma por onde passa toda leitura declarativa (o
# regex_seguro.buscar_async lê por ela). Um painel validando um arquivo hostil tomaria uma
# thread dela por requisição e travaria o poll de todo aparelho. Uma thread própria é o limite
# inteiro: dois integradores salvando ao mesmo tempo entram na fila um do outro em vez de
# dobrar o trabalho, e nenhuma leitura espera atrás deles.
FILA = ThreadPoolExecutor(max_workers=1, thread_name_prefix="iphub-declarativos")

CHAVE_ARQUIVO = "json"

CORPO_INVALIDO = "corpo_invalido"
ERRO_INTERNO = "erro_interno"
DECL_EM_USO = "decl_em_uso"
DECL_NAO_ENCONTRADO = "decl_nao_encontrado"

# The starting point of each transport, so nobody writes a driver from a blank box. Each one
# is a file that passes the validation, which a test asserts: a template the daemon refuses
# would teach the format wrong on the very first save.
# O ponto de partida de cada transporte, para ninguém escrever driver de caixa vazia. Cada um
# é um arquivo que passa na validação, o que um teste garante: um modelo que o daemon recusa
# ensinaria o formato errado logo no primeiro salvamento.
MODELOS: dict[str, dict] = {
    "tcp": {
        "manifesto": {
            "tipo": "meu_aparelho_tcp",
            "rotulo": {"pt": "Meu aparelho TCP", "en": "My TCP device"},
            "categoria": "outro",
            "capacidades": ["ligar", "desligar", "fonte"],
            "textos": {
                "pt": {"descricao": "Aparelho que fala uma linha de texto numa porta TCP."},
                "en": {"descricao": "Device speaking one line of text on a TCP port."},
            },
        },
        "transporte": {
            "tcp": {"porta": 23, "terminador": "\r", "timeout_s": 3, "intervalo_min_ms": 200}
        },
        "comandos": {
            "ligar": {"envia": "PWR ON"},
            "desligar": {"envia": "PWR OFF"},
            "fonte": {"envia": "SRC {valor}", "valores": {"HDMI 1": "1", "HDMI 2": "2"}},
        },
        "estado": {
            "pede": [{"envia": "PWR?"}, {"envia": "SRC?"}],
            "le": {
                "ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"},
                "fonte": {"regex": "SRC ([0-9])"},
            },
        },
    },
    "http": {
        "manifesto": {
            "tipo": "meu_aparelho_http",
            "rotulo": {"pt": "Meu aparelho HTTP", "en": "My HTTP device"},
            "categoria": "outro",
            "capacidades": ["ligar", "desligar"],
            "config_campos": [{"nome": "chave", "tipo": "segredo", "obrigatorio": False}],
            "textos": {
                "pt": {
                    "descricao": "Aparelho que aceita HTTP e responde o estado em JSON.",
                    "campo_chave": (
                        "Chave do aparelho. Vai no cabecalho de cada requisicao e nunca sai "
                        "do daemon."
                    ),
                },
                "en": {
                    "descricao": "Device taking HTTP and answering its state in JSON.",
                    "campo_chave": (
                        "Key of the device. It travels in the header of every request and "
                        "never leaves the daemon."
                    ),
                },
            },
        },
        "transporte": {
            "http": {
                "base": "http://{ip}",
                "metodo": "GET",
                "timeout_s": 4,
                "cabecalhos": {"X-Api-Key": "chave"},
            }
        },
        "comandos": {"ligar": {"envia": "/on"}, "desligar": {"envia": "/off"}},
        "estado": {
            "pede": [{"envia": "/status.json"}],
            "le": {"ligado": {"json": "ligado", "verdadeiro": "true"}},
        },
    },
    "udp": {
        "manifesto": {
            "tipo": "meu_aparelho_udp",
            "rotulo": {"pt": "Meu aparelho UDP", "en": "My UDP device"},
            "categoria": "outro",
            "capacidades": ["ligar", "desligar", "volume"],
            "textos": {
                "pt": {"descricao": "Aparelho que recebe uma linha de texto por datagrama."},
                "en": {"descricao": "Device taking one line of text per datagram."},
            },
        },
        "transporte": {
            "udp": {"porta": 50000, "terminador": "\r", "timeout_s": 2, "intervalo_min_ms": 100}
        },
        "escala_volume": {"min": 0, "max": 79},
        "comandos": {
            "ligar": {"envia": "PWR ON"},
            "desligar": {"envia": "PWR OFF"},
            "volume": {"envia": "VOL {valor_escala}"},
        },
        "estado": {
            "pede": [{"envia": "PWR?"}, {"envia": "VOL?"}],
            "le": {
                "ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"},
                "volume": {"regex": "VOL ([0-9]{1,2})"},
            },
        },
    },
}


def _problemas(problemas: tuple[tuple[str, str], ...]) -> web.Response:
    """One refusal per field, all of them at once, never a phrase (sections 7 and 11).

    Uma recusa por campo, todas de uma vez, nunca uma frase (seções 7 e 11).
    """
    return web.json_response(
        {
            "ok": False,
            "code": DECL_INVALIDO,
            "problemas": [{"campo": campo, "codigo": codigo} for campo, codigo in problemas],
        },
        status=400,
    )


def _driver_json(declarativo: Declarativo, em_uso: bool) -> dict:
    """What the panel needs to draw one driver and to know which buttons it may offer.

    O que o painel precisa para desenhar um driver e saber que botões pode oferecer.
    """
    return {
        "tipo": declarativo.tipo,
        "origem": declarativo.origem,
        "em_uso": em_uso,
        "manifesto": manifesto_json(declarativo.definicao.manifesto),
    }


def _tipos_em_uso(app: web.Application) -> set[str]:
    return {cadastro.tipo for cadastro in app[GESTOR].cadastros}


async def _arquivo(request: web.Request) -> dict | None:
    """The driver file out of the body, or None when the body is not one.

    O arquivo de driver dentro do corpo, ou None quando o corpo não é um.
    """
    corpo = await ler_corpo(request, maximo=CORPO_MAXIMO_DRIVER)
    if corpo is None:
        return None
    arquivo = corpo.get(CHAVE_ARQUIVO)
    return arquivo if isinstance(arquivo, dict) else None


async def _aceitar(app: web.Application, dados: dict) -> Definicao | web.Response:
    """The declaration as typed data, or the refusal the panel shows field by field.

    A declaração como dado tipado, ou a recusa que o painel mostra campo a campo.
    """
    # Why: the fire test of section 7 runs the pattern in another process and waits for it, so
    # validating on the event loop would stop the poll of every device and the panel with it.
    # Por que: a prova de fogo da seção 7 roda o padrão em outro processo e espera por ele,
    # então validar no laço de eventos pararia o poll de todo aparelho e o painel junto.
    try:
        definicao = await _na_fila(
            validar_declaracao, dados, regex=regex_seguro.instancia_validacao()
        )
    except DeclaracaoInvalida as erro:
        return _problemas(erro.problemas)
    if definicao.manifesto.tipo in catalogo_de(app).nativos:
        # Why: rule 3 of section 2, data never replaces code that ships in the image.
        # Por que: regra 3 da seção 2, dado nunca substitui código que embarca na imagem.
        return _problemas(((CAMPO_TIPO, DECL_TIPO_OCUPADO),))
    return definicao


async def _na_fila[T](funcao: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Runs the heavy declarative work on the single thread of FILA, never on the loop.

    Roda o trabalho declarativo pesado na única thread da FILA, nunca no laço.
    """
    laco = asyncio.get_running_loop()
    return await laco.run_in_executor(FILA, functools.partial(funcao, *args, **kwargs))


async def _reler(catalogo: Catalogo) -> None:
    """Re reads both directories, without touching the equipment already mounted.

    Relê as duas pastas, sem tocar no equipamento já montado.
    """
    # Why: the reload validates every file again, fire test included, which is another process
    # and a deadline per pattern; that is not work to do on the event loop.
    # Por que: a recarga valida todo arquivo de novo, com prova de fogo, que é outro processo e
    # um prazo por padrão; isso não é trabalho para o laço de eventos.
    await _na_fila(catalogo.recarregar)


async def _recarregar(app: web.Application, tipo: str) -> None:
    """Re reads both directories and rebuilds the equipment of one tipo, with no restart.

    Relê as duas pastas e refaz o equipamento de um tipo, sem reiniciar.
    """
    catalogo = catalogo_de(app)
    await _reler(catalogo)
    await app[GESTOR].trocar_catalogo(catalogo.drivers, refazer=(tipo,))


def _recusas_de(catalogo: Catalogo, caminho: Path) -> tuple[tuple[str, str], ...]:
    """What the loader refused about ONE file, with no repeated pair.

    O que o carregador recusou sobre UM arquivo, sem par repetido.
    """
    return tuple(
        dict.fromkeys(
            problema
            for recusado in catalogo.recusados
            if recusado.arquivo == caminho
            for problema in recusado.problemas
        )
    )


def _resultado(
    catalogo: Catalogo, tipo: str, caminho: Path, servidos: frozenset[Path]
) -> tuple[tuple[str, str], ...] | None:
    """None when the save worked, or the problems to answer for THIS file.

    None quando o salvamento funcionou, ou os problemas a responder por ESTE arquivo.
    """
    declarativo = catalogo.declarativos.get(tipo)
    if declarativo is None or declarativo.arquivo != caminho:
        # Why: ok is ok only when the catalog serves THIS tipo out of THIS file. A refusal the
        # file already carried before the save is still a refusal of it, and answering ok to
        # that hands the panel a driver nobody can use.
        # Por que: ok só é ok quando o catálogo serve ESTE tipo a partir DESTE arquivo. Uma
        # recusa que o arquivo já carregava antes do salvamento segue sendo recusa dele, e
        # responder ok a isso entrega ao painel um driver que ninguém consegue usar.
        return _recusas_de(catalogo, caminho) or ((CAMPO_ARQUIVO, DECL_INVALIDO),)
    # Why: the loader hands a tipo and a discovery signature to whichever name comes first in
    # the directory, so this save can push a driver that WORKED out of the catalog. That is
    # undone, and the fields named (manifesto.tipo, descoberta) are fields of THIS file too:
    # they are what it disputes. A file that was ALREADY refused before the save is not one
    # this save broke, and it never rolls a good save back.
    # Por que: o carregador entrega um tipo e uma assinatura de descoberta a quem vem primeiro
    # na pasta, então este salvamento pode empurrar para fora do catálogo um driver que
    # FUNCIONAVA. Isso é desfeito, e os campos nomeados (manifesto.tipo, descoberta) também são
    # campos DESTE arquivo: são o que ele disputa. Um arquivo JÁ recusado antes do salvamento
    # não é um que este salvamento quebrou, e ele nunca desfaz um salvamento bom.
    perdidos = tuple(
        dict.fromkeys(
            problema
            for recusado in catalogo.recusados
            if recusado.arquivo != caminho and recusado.arquivo in servidos
            for problema in recusado.problemas
        )
    )
    return perdidos or None


def _desfazer(caminho: Path, anterior: str | None) -> None:
    """Puts back what was there before a save the loader ended up refusing.

    Devolve o que estava lá antes de um salvamento que o carregador acabou recusando.
    """
    # Why: the validation and the loader judge different things (a signature already claimed is
    # only visible to the loader), so a file may pass one and be refused by the other; leaving
    # it on the disk would replace a driver that works with one the hub does not load.
    # Por que: a validação e o carregador julgam coisas diferentes (uma assinatura já
    # reivindicada só o carregador vê), então um arquivo pode passar por uma e ser recusado
    # pelo outro; deixá-lo no disco trocaria um driver que funciona por um que o hub não carrega.
    try:
        if anterior is None:
            caminho.unlink(missing_ok=True)
        else:
            escrever_texto(caminho, anterior)
    except OSError as erro:
        log.error("could not undo the save of %s: %s", caminho.name, erro)


def _texto_do_arquivo(dados: dict) -> str:
    """The bytes the file ends up holding: indented, so it is still a file to read and edit.

    Os bytes que o arquivo passa a ter: indentado, para ele seguir sendo arquivo de ler e editar.
    """
    return json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _conteudo(caminho: Path) -> str | None:
    """What the file holds now, for a save the loader may end up refusing.

    O que o arquivo tem agora, para um salvamento que o carregador pode acabar recusando.
    """
    try:
        return ler_texto(caminho)
    except (OSError, ValueError):
        # Why: a file nobody can read is a file the loader was already refusing, and a save
        # must not fail because of it; there is simply nothing to put back.
        # Por que: um arquivo que ninguém lê é um arquivo que o carregador já recusava, e um
        # salvamento não pode falhar por causa dele; simplesmente não há o que devolver.
        return None


async def _gravar(app: web.Application, definicao: Definicao, dados: dict) -> web.Response:
    catalogo = catalogo_de(app)
    pasta = catalogo.pasta_integrador
    if pasta is None:
        log.error("this hub has no drivers directory of its own to save into")
        return resposta_erro(500, ERRO_INTERNO)
    tipo = definicao.manifesto.tipo
    texto = _texto_do_arquivo(dados)
    if len(texto.encode("utf-8")) > ARQUIVO_MAXIMO:
        # Why: the loader refuses a file past this ceiling, so saving one would write a driver
        # that never loads again, and the hub would list it only until the next boot.
        # Por que: o carregador recusa arquivo acima deste teto, então salvar um gravaria um
        # driver que nunca mais carrega, e o hub o listaria só até o próximo boot.
        return _problemas(((CAMPO_ARQUIVO, DECL_ARQUIVO_GRANDE),))
    caminho = pasta / f"{tipo}.json"
    # Why: what the loader is serving RIGHT NOW, not what it was serving at the last reload. A
    # file that changed on the disk since then, or one somebody dropped in the directory with a
    # shell, would otherwise read as a driver THIS save pushed out, and a good save would be
    # undone to blame a neighbour the integrator never touched.
    # Por que: o que o carregador está servindo AGORA, não o que ele servia na última recarga.
    # Um arquivo que mudou no disco desde então, ou um que alguém largou na pasta por um shell,
    # seria lido como um driver que ESTE salvamento empurrou para fora, e um salvamento bom
    # seria desfeito para culpar um vizinho em que o integrador nunca tocou.
    await _reler(catalogo)
    servidos = frozenset(driver.arquivo for driver in catalogo.declarativos.values())
    anterior = _conteudo(caminho)
    try:
        garantir_diretorio(pasta)
        escrever_texto(caminho, texto)
    except OSError as erro:
        log.error("could not save the declarative driver %s: %s", tipo, erro)
        return resposta_erro(500, ERRO_INTERNO)
    await _recarregar(app, tipo)
    recusa = _resultado(catalogo, tipo, caminho, servidos)
    if recusa is not None:
        _desfazer(caminho, anterior)
        await _recarregar(app, tipo)
        return _problemas(recusa)
    return resposta_ok()


@com_sessao
async def listar(request: web.Request) -> web.Response:
    catalogo = catalogo_de(request.app)
    usados = _tipos_em_uso(request.app)
    return resposta_ok(
        drivers=[
            _driver_json(declarativo, tipo in usados)
            for tipo, declarativo in sorted(catalogo.declarativos.items())
        ]
    )


@com_sessao
async def validar(request: web.Request) -> web.Response:
    dados = await _arquivo(request)
    if dados is None:
        return resposta_erro(400, CORPO_INVALIDO)
    resultado = await _aceitar(request.app, dados)
    return resultado if isinstance(resultado, web.Response) else resposta_ok()


@com_sessao
async def salvar(request: web.Request) -> web.Response:
    app = request.app
    dados = await _arquivo(request)
    if dados is None:
        return resposta_erro(400, CORPO_INVALIDO)
    async with app[TRAVA_DRIVERS]:
        resultado = await _aceitar(app, dados)
        if isinstance(resultado, web.Response):
            return resultado
        return await _gravar(app, resultado, dados)


@com_sessao
async def remover(request: web.Request) -> web.Response:
    app = request.app
    tipo = request.match_info["tipo"]
    async with app[TRAVA_DRIVERS]:
        declarativo = catalogo_de(app).declarativos.get(tipo)
        # Why: a driver of the image is not on the disk this route writes to, and a tipo that
        # no file of the integrator claims has nothing here to delete; both answer the same,
        # because the panel offers the button by origem and asks nothing else of the daemon.
        # Por que: um driver da imagem não está no disco em que esta rota grava, e um tipo que
        # arquivo nenhum do integrador reivindica não tem o que apagar aqui; os dois respondem
        # igual, porque o painel oferece o botão pela origem e não pede mais nada ao daemon.
        if declarativo is None or declarativo.origem != ORIGEM_INTEGRADOR:
            return resposta_erro(404, DECL_NAO_ENCONTRADO)
        if tipo in _tipos_em_uso(app):
            # Why: deleting the driver of a registered equipment would leave the panel showing
            # a device of an unknown tipo, offline forever and with no way back.
            # Por que: apagar o driver de um equipamento cadastrado deixaria o painel mostrando
            # um aparelho de tipo desconhecido, offline para sempre e sem volta.
            return resposta_erro(409, DECL_EM_USO)
        try:
            declarativo.arquivo.unlink(missing_ok=True)
        except OSError as erro:
            log.error("could not delete the declarative driver %s: %s", tipo, erro)
            return resposta_erro(500, ERRO_INTERNO)
        await _recarregar(app, tipo)
    return resposta_ok()


@com_sessao
async def modelo(request: web.Request) -> web.Response:
    escolhido = MODELOS.get(request.match_info["transporte"])
    if escolhido is None:
        return resposta_erro(404, DECL_NAO_ENCONTRADO)
    return resposta_ok(modelo=escolhido)
