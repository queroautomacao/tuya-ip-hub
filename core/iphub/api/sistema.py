# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The appliance itself: restarting the daemon and knowing whether a newer image exists.

Runs the container as a user that is not root, with no docker socket mounted, so
the daemon cannot replace its own image; what it can do is stop cleanly, which the container
policy answers by starting it again, and say which version it is and which one is published.
Applying an update is a command on the host, and the panel shows it instead of pretending.
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

# The answer leaves before the process goes, so the panel reads an ok instead of a
# connection that dropped, and long enough for the socket to flush it.
ATRASO_REINICIO_S = 0.5

# A fixed address of the public releases, never one the request carries, so the hub is
# not a client of whatever host a body names;.
URL_ULTIMA_VERSAO = "https://api.github.com/repos/queroautomacao/tuya-ip-hub/releases/latest"
PRAZO_VERSAO_S = 5.0
CORPO_MAXIMO = 64 * 1024
# A hub on a customer LAN asks once every ten minutes at most, whoever is looking at the
# panel; a check on every page load would be traffic to the internet on somebody else's link.
CACHE_VERSAO_S = 600.0

_VERSAO = re.compile(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")


def encerrar_processo() -> None:
    """The clean stop of run_app: SIGTERM to ourselves, which drains the routes and the bus."""
    os.kill(os.getpid(), signal.SIGTERM)


def partes_de(versao: object) -> tuple[int, int, int] | None:
    """The three numbers of a version, or None for text that is not one."""
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


# A project with no release yet is answered with a 404, which is the internet answering
# and not the internet missing; the two must read differently on the panel, so the first is
# an empty version and the second is None.
SEM_RELEASE = ""


async def buscar_ultima_versao_no_github() -> str | None:
    """The tag of the latest public release, SEM_RELEASE when there is none yet, or None when
    the internet did not answer.
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
    """Answers, then stops the daemon; the container policy brings it back."""
    log.warning("restart requested from the panel")
    asyncio.get_running_loop().call_later(ATRASO_REINICIO_S, request.app[ENCERRAR])
    return resposta_ok()


@com_sessao
async def atualizacao(request: web.Request) -> web.Response:
    """The version this daemon is, the latest one published, and whether it is newer."""
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
