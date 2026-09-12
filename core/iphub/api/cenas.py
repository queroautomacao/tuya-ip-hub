# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Over HTTP: the thirty two scenes of the installation, read, saved and run.

A scene is data and never program, so nothing here interprets one: the module of the scenes
validates the whole list at once and answers every problem as (campo, codigo), the same way
a driver file is refused, and these routes only turn that into a status. The
executor is the one that runs the steps, on a task of its own, so a scene that waits for a
projector to warm up does not hold the request that started it.

The POSITION of a scene is its number, so a save takes the whole list and never one scene:
saving one alone would need an index anyway, and a list that came back shorter would move
scene 3 into slot 2 in every automation the customer already built on the platform.
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

# The ceiling keeps a path of a thousand digits from becoming a thousand digit int.
_SO_DIGITOS = re.compile(r"[0-9]{1,10}")


CENAS_INVALIDAS = "cenas_invalidas"

# Thirty two scenes of up to sixty four steps do not fit the body of a login, and the
# ceiling of the common reader would truncate an honest list into a refusal nobody could fix.
CORPO_MAXIMO_CENAS = 256 * 1024

STATUS_POR_CODIGO = {
    CENAS_INVALIDAS: 400,
    modulo.CENA_NAO_ENCONTRADA: 404,
    modulo.CENA_EM_CURSO: 409,
}


def _erro(codigo: str) -> web.Response:
    return resposta_erro(STATUS_POR_CODIGO.get(codigo, 500), codigo)


def _problemas(problemas: tuple[tuple[str, str], ...]) -> web.Response:
    """Every problem of the list at once, so a refusal is fixed in one pass."""
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
    # The integrator is at the keyboard, so a step over an identity nobody registered is
    # refused now instead of becoming a button that never does anything.
    identidades = {cadastro.identidade for cadastro in app[GESTOR].cadastros}
    try:
        cenas = modulo.validar(dados.get("cenas"), identidades)
    except modulo.CenasInvalidas as recusa:
        return _problemas(recusa.problemas)
    # A scene that lived only in memory would be gone on the next boot while the
    # automation that calls it on the platform stayed, and the customer would press a button
    # that does nothing; the file is written before the executor takes the list.
    try:
        trocar_config(app, replace(config_de(app), cenas=cenas))
    except OSError as falha:
        log.error("could not write the scenes: %s", falha)
        return erro(ERRO_INTERNO)
    cenas_de(app).trocar(cenas)
    return resposta_ok()


@com_sessao
async def executar(request: web.Request) -> web.Response:
    """Answers at once: the steps run on a task of their own, in the order they were saved."""
    bruto = request.match_info["numero"]
    # Str.isdigit is true for characters int refuses, such as the superscript two,
    # so a session holder could turn any route with a number in the path into a 500 with a
    # traceback in the log. Only ASCII digits are a number here.
    numero = int(bruto) if _SO_DIGITOS.fullmatch(bruto) else bruto
    codigo = cenas_de(request.app).executar(numero)
    return resposta_ok() if codigo is None else _erro(codigo)
