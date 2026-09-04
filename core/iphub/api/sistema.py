# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The appliance itself: restarting the daemon and knowing whether a newer image exists.

Section 9 runs the container as a user that is not root, with no docker socket mounted, so
the daemon cannot replace its own image; what it can do is stop cleanly, which the container
policy answers by starting it again, and say which version it is and which one is published.
Applying an update is a command on the host, and the panel shows it instead of pretending.

O próprio appliance: reiniciar o daemon e saber se existe imagem mais nova.

A seção 9 roda o container como usuário que não é root, sem o socket do docker montado, então
o daemon não consegue trocar a própria imagem; o que ele consegue é parar limpo, que a
política do container responde subindo de novo, e dizer qual versão ele é e qual está
publicada. Aplicar uma atualização é um comando no host, e o painel o mostra em vez de fingir.
"""

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from iphub.api.comum import Mutavel, com_sessao, resposta_ok
from iphub.drivers import corpo
from iphub.versao import VERSAO

log = logging.getLogger("iphub.api.sistema")

type Encerrar = Callable[[], None]
type BuscarVersao = Callable[[], Awaitable[str | None]]

ENCERRAR = web.AppKey("encerrar", Encerrar)
BUSCAR_ULTIMA_VERSAO = web.AppKey("buscar_ultima_versao", BuscarVersao)
CACHE_ATUALIZACAO = web.AppKey("cache_atualizacao", Mutavel[tuple[float, str | None] | None])

# Why: the answer leaves before the process goes, so the panel reads an ok instead of a
# connection that dropped, and long enough for the socket to flush it.
# Por que: a resposta sai antes de o processo ir, então o painel lê um ok em vez de uma
# conexão que caiu, e tempo bastante para o socket a despachar.
ATRASO_REINICIO_S = 0.5

# Why: a fixed address of the public releases, never one the request carries, so the hub is
# not a client of whatever host a body names; section 9.
# Por que: um endereço fixo das releases públicas, nunca um que a requisição carregue, para o
# hub não ser cliente do host que um corpo nomear; seção 9.
URL_ULTIMA_VERSAO = "https://api.github.com/repos/queroautomacao/tuya-ip-hub/releases/latest"
PRAZO_VERSAO_S = 5.0
CORPO_MAXIMO = 64 * 1024
# Why: a hub on a customer LAN asks once every ten minutes at most, whoever is looking at the
# panel; a check on every page load would be traffic to the internet on somebody else's link.
# Por que: um hub na LAN de um cliente pergunta no máximo uma vez a cada dez minutos, quem
# quer que esteja olhando o painel; uma checagem a cada abertura de página seria tráfego para
# a internet no link de outra pessoa.
CACHE_VERSAO_S = 600.0

_VERSAO = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")


def encerrar_processo() -> None:
    """The clean stop of run_app: SIGTERM to ourselves, which drains the routes and the bus.

    A parada limpa do run_app: SIGTERM para nós mesmos, que esvazia as rotas e o barramento.
    """
    os.kill(os.getpid(), signal.SIGTERM)


def partes_de(versao: object) -> tuple[int, int, int] | None:
    """The three numbers of a version, or None for text that is not one.

    Os três números de uma versão, ou None para texto que não é uma.
    """
    if not isinstance(versao, str):
        return None
    casado = _VERSAO.fullmatch(versao.strip())
    if casado is None:
        return None
    maior, menor, correcao = casado.groups()
    return int(maior), int(menor), int(correcao)


def ha_mais_nova(atual: str, ultima: str | None) -> bool:
    de = partes_de(atual)
    para = partes_de(ultima)
    return de is not None and para is not None and para > de


# Why: a project with no release yet is answered with a 404, which is the internet answering
# and not the internet missing; the two must read differently on the panel, so the first is
# an empty version and the second is None.
# Por que: um projeto ainda sem release é respondido com 404, que é a internet respondendo e
# não a internet faltando; os dois precisam se ler diferente no painel, então o primeiro é
# uma versão vazia e o segundo é None.
SEM_RELEASE = ""


async def buscar_ultima_versao_no_github() -> str | None:
    """The tag of the latest public release, SEM_RELEASE when there is none yet, or None when
    the internet did not answer.

    A tag da última release pública, SEM_RELEASE quando ainda não há nenhuma, ou None quando
    a internet não respondeu.
    """
    cabecalhos = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"tuya-ip-hub/{VERSAO}",
    }
    try:
        async with ClientSession(timeout=ClientTimeout(total=PRAZO_VERSAO_S)) as sessao:
            async with sessao.get(
                URL_ULTIMA_VERSAO, headers=cabecalhos, allow_redirects=False
            ) as resposta:
                if resposta.status == 404:
                    return SEM_RELEASE
                if resposta.status != 200:
                    return None
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
    except (TimeoutError, ClientError, OSError):
        return None
    try:
        dados = json.loads(bruto)
    except ValueError:
        return None
    if not isinstance(dados, dict):
        return None
    partes = partes_de(dados.get("tag_name"))
    return None if partes is None else ".".join(str(n) for n in partes)


@com_sessao
async def reiniciar(request: web.Request) -> web.Response:
    """Answers, then stops the daemon; the container policy brings it back.

    Responde, depois para o daemon; a política do container o traz de volta.
    """
    log.warning("restart requested from the panel")
    asyncio.get_running_loop().call_later(ATRASO_REINICIO_S, request.app[ENCERRAR])
    return resposta_ok()


@com_sessao
async def atualizacao(request: web.Request) -> web.Response:
    """The version this daemon is, the latest one published, and whether it is newer.

    A versão que este daemon é, a última publicada, e se ela é mais nova.
    """
    cache = request.app[CACHE_ATUALIZACAO]
    agora = time.monotonic()
    guardado = cache.valor
    if guardado is None or agora - guardado[0] >= CACHE_VERSAO_S:
        ultima = await request.app[BUSCAR_ULTIMA_VERSAO]()
        cache.valor = (agora, ultima)
    else:
        ultima = guardado[1]
    return resposta_ok(
        atual=VERSAO,
        ultima=ultima or None,
        disponivel=ha_mais_nova(VERSAO, ultima),
        verificada=ultima is not None,
    )
