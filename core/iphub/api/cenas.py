# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 over HTTP: the thirty two scenes of the installation, read, saved and run.

A scene is data and never program, so nothing here interprets one: the module of the scenes
validates the whole list at once and answers every problem as (campo, codigo), the same way
a driver file is refused in section 7, and these routes only turn that into a status. The
executor is the one that runs the steps, on a task of its own, so a scene that waits for a
projector to warm up does not hold the request that started it.

The POSITION of a scene is its number, so a save takes the whole list and never one scene:
saving one alone would need an index anyway, and a list that came back shorter would move
scene 3 into slot 2 in every automation the customer already built on the platform.

Seção 8 sobre HTTP: as trinta e duas cenas da instalação, lidas, salvas e executadas.

Uma cena é dado e nunca programa, então nada aqui interpreta uma: o módulo das cenas valida a
lista inteira de uma vez e responde todo problema como (campo, codigo), do mesmo jeito que um
arquivo de driver é recusado na seção 7, e estas rotas só transformam isso num status. Quem
roda os passos é o executor, numa tarefa própria, para uma cena que espera um projetor
aquecer não segurar a requisição que a começou.

A POSIÇÃO de uma cena é o número dela, então um salvamento leva a lista inteira e nunca uma
cena: salvar uma sozinha precisaria de um índice de todo jeito, e uma lista que voltasse mais
curta moveria a cena 3 para a vaga 2 em toda automação que o cliente já montou na plataforma.
"""

import logging
import re
from dataclasses import replace

from aiohttp import web

from iphub import cenas as modulo
from iphub.api.comum import (
    GESTOR,
    cenas_de,
    com_sessao,
    config_de,
    ler_corpo,
    resposta_ok,
    trocar_config,
)
from iphub.api.licencas import CORPO_INVALIDO, ERRO_INTERNO, erro
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.api.cenas")

# Why: the ceiling keeps a path of a thousand digits from becoming a thousand digit int.
# Por que: o teto impede que um caminho de mil dígitos vire um int de mil dígitos.
_SO_DIGITOS = re.compile(r"[0-9]{1,10}")


CENAS_INVALIDAS = "cenas_invalidas"

# Why: thirty two scenes of up to sixty four steps do not fit the body of a login, and the
# ceiling of the common reader would truncate an honest list into a refusal nobody could fix.
# Por que: trinta e duas cenas de até sessenta e quatro passos não cabem no corpo de um login,
# e o teto do leitor comum truncaria uma lista honesta numa recusa que ninguém consegue
# consertar.
CORPO_MAXIMO_CENAS = 256 * 1024

STATUS_POR_CODIGO = {
    CENAS_INVALIDAS: 400,
    modulo.CENA_NAO_ENCONTRADA: 404,
    modulo.CENA_EM_CURSO: 409,
}


def _erro(codigo: str) -> web.Response:
    return resposta_erro(STATUS_POR_CODIGO.get(codigo, 500), codigo)


def _problemas(problemas: tuple[tuple[str, str], ...]) -> web.Response:
    """Every problem of the list at once, so a refusal is fixed in one pass.

    Todo problema da lista de uma vez, para uma recusa ser consertada numa passada.
    """
    return web.json_response(
        {
            "ok": False,
            "code": CENAS_INVALIDAS,
            "problemas": [{"campo": campo, "codigo": codigo} for campo, codigo in problemas],
        },
        status=STATUS_POR_CODIGO[CENAS_INVALIDAS],
    )


def _cena_json(numero: int, cena: modulo.Cena, em_curso: bool) -> dict:
    return {
        "numero": numero,
        "nome": cena.nome,
        "intervalo_ms": cena.intervalo_ms,
        "em_curso": em_curso,
        "passos": [
            {
                "equipamento": passo.equipamento,
                "acao": passo.acao,
                "valor": passo.valor,
                "espera_ms": passo.espera_ms,
            }
            for passo in cena.passos
        ],
    }


@com_sessao
async def listar(request: web.Request) -> web.Response:
    executor = cenas_de(request.app)
    return resposta_ok(
        cenas=[
            _cena_json(numero, cena, executor.em_curso(numero))
            for numero, cena in enumerate(executor.cenas, start=1)
        ],
        maximo=modulo.MAXIMO,
        acoes=list(modulo.ACOES),
        passos_maximos=modulo.PASSOS_MAXIMOS,
        espera_maxima_ms=modulo.ESPERA_MAXIMA_MS,
        intervalo_padrao_ms=modulo.INTERVALO_PADRAO_MS,
    )


@com_sessao
async def salvar(request: web.Request) -> web.Response:
    app = request.app
    dados = await ler_corpo(request, maximo=CORPO_MAXIMO_CENAS)
    if dados is None:
        return erro(CORPO_INVALIDO)
    # Why: the integrator is at the keyboard, so a step over an identity nobody registered is
    # refused now instead of becoming a button that never does anything.
    # Por que: o integrador está no teclado, então um passo sobre uma identidade que ninguém
    # cadastrou é recusado agora em vez de virar um botão que nunca faz nada.
    identidades = {cadastro.identidade for cadastro in app[GESTOR].cadastros}
    try:
        cenas = modulo.validar(dados.get("cenas"), identidades)
    except modulo.CenasInvalidas as recusa:
        return _problemas(recusa.problemas)
    # Why: a scene that lived only in memory would be gone on the next boot while the
    # automation that calls it on the platform stayed, and the customer would press a button
    # that does nothing; the file is written before the executor takes the list.
    # Por que: uma cena que vivesse só na memória sumiria no próximo boot enquanto a automação
    # que a chama na plataforma continuaria, e o cliente apertaria um botão que não faz nada;
    # o arquivo é gravado antes de o executor assumir a lista.
    try:
        trocar_config(app, replace(config_de(app), cenas=cenas))
    except OSError as falha:
        log.error("could not write the scenes: %s", falha)
        return erro(ERRO_INTERNO)
    cenas_de(app).trocar(cenas)
    return resposta_ok()


@com_sessao
async def executar(request: web.Request) -> web.Response:
    """Answers at once: the steps run on a task of their own, in the order they were saved.

    Responde na hora: os passos correm numa tarefa própria, na ordem em que foram salvos.
    """
    bruto = request.match_info["numero"]
    # Why: str.isdigit() is true for characters int() refuses, such as the superscript two,
    # so a session holder could turn any route with a number in the path into a 500 with a
    # traceback in the log. Only ASCII digits are a number here.
    # Por que: str.isdigit() é verdadeiro para caracteres que o int() recusa, como o dois
    # sobrescrito, então quem tem sessão podia transformar qualquer rota com número no caminho
    # num 500 com traceback no log. Só dígito ASCII é número aqui.
    numero = int(bruto) if _SO_DIGITOS.fullmatch(bruto) else bruto
    codigo = cenas_de(request.app).executar(numero)
    return resposta_ok() if codigo is None else _erro(codigo)
