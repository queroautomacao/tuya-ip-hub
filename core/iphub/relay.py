# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The transport of the remote access: a socket to the relay of the maker.

While the window is open the hub keeps ONE outbound socket to the relay of the maker, gets
back an address for it, and answers the requests that arrive on it. Nothing is forwarded on
the router of the customer, nothing listens on the public address of the house, and there is
no per hub object anywhere to create, which is what lets a fleet grow without a ceiling.

Two properties this file is built around:

- what comes back from the relay is a request from the internet, and it is treated as one. It
  is not dispatched inside this process: it goes out and comes back through the daemon's own
  door, on the loopback, so the Host check, the Origin check, the session guard, the window
  code and the second factor all run exactly as they run for a request on the local network.
  A shortcut here would be a door beside the door;
- the relay never decides anything about this hub. It carries bytes. Everything that says who
  may enter is checked at this end, which is why the relay can be a service that asks no hub
  to prove anything.
"""

import asyncio
import base64
import contextlib
import ipaddress
import json
import logging
from collections.abc import Callable
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType

from iphub.drivers import corpo as leitor_de_corpo
from iphub.portao import CABECALHO_DO_RELAY

log = logging.getLogger("iphub.relay")

CAMINHO_DO_HUB = "/hub"

OLA = "ola"
PRONTO = "pronto"
PEDIDO = "pedido"
RESPOSTA = "resposta"
VERSAO = 1
# The close code the relay answers a home it does not serve with.
RECUSADO = 4403

CORPO_MAXIMO = 8 * 1024 * 1024
QUADRO_MAXIMO = 12 * 1024 * 1024
# The handshake with the relay and each trip to the loopback are bounded by the same clock;
# the socket itself, once up, lives until the window closes or the link dies.
PRAZO_S = 8.0
ESPERA_INICIAL_S = 2.0
ESPERA_MAXIMA_S = 60.0
# A relay that refused the home will refuse it again in a minute; a hub that keeps asking is
# a fleet of them hammering the service of the maker for nothing.
ESPERA_APOS_RECUSA_S = 600.0
# A socket that has been up this long is a socket that connected, not one looping.
ESTAVEL_S = 60.0

# What this end decides and the far end may not: the authority of the request, the framing
# of its body, and the hop by hop headers that belong to one connection and not to the next.
CABECALHOS_NOSSOS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "upgrade",
        "te",
        "trailer",
        "proxy-connection",
        "proxy-authorization",
    }
)


def endereco_aceitavel(base: str) -> bool:
    """HTTPS, or plain HTTP only to this very machine, which is what a test and a bench use.

    The socket to the relay carries the password, the code of the window and the code of the
    authenticator of whoever logs in from outside; on the open internet it goes encrypted or
    it does not go.
    """
    partes = urlsplit(base)
    if partes.scheme == "https":
        return bool(partes.hostname)
    if partes.scheme != "http" or not partes.hostname:
        return False
    if partes.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(partes.hostname).is_loopback
    except ValueError:
        return False


class Cliente:
    """One socket to the relay, and the address it answered with, while the window is open."""

    def __init__(
        self,
        base_de: Callable[[], str],
        porta_local: int,
        home_de: Callable[[], str] = str,
    ) -> None:
        self._base_de = base_de
        self._porta_local = porta_local
        # The home of the app this hub tells the relay it belongs to, read at the moment of
        # dialling, so a home written after the window opened counts on the next dial.
        self._home_de = home_de
        self._sessao: ClientSession | None = None
        self._tarefa: asyncio.Task | None = None
        # The answers in flight, held here so the loop never collects one half way through.
        self._respostas: set[asyncio.Task] = set()
        self._url = ""
        self._aberto_em = 0.0
        self._espera_s = ESPERA_INICIAL_S
        self._recusado = False

    # The shape the service of the window drives.

    def aberto(self) -> bool:
        return self._tarefa is not None and not self._tarefa.done()

    def morreu(self) -> bool:
        return self._tarefa is not None and self._tarefa.done()

    def estavel(self) -> bool:
        laco = asyncio.get_running_loop()
        return self.aberto() and laco.time() - self._aberto_em >= ESTAVEL_S

    def espera_s(self) -> float:
        return ESPERA_APOS_RECUSA_S if self._recusado else self._espera_s

    def adiar(self) -> None:
        self._espera_s = min(ESPERA_MAXIMA_S, self._espera_s * 2)

    def recomecar_a_espera(self) -> None:
        self._espera_s = ESPERA_INICIAL_S
        self._recusado = False

    def recusado(self) -> bool:
        """Whether the last dial ended with the relay refusing the home of this hub."""
        return self._recusado

    def url(self) -> str:
        """The address of this window, which is what the app of the customer shows."""
        return self._url if self.aberto() else ""

    async def abrir(self) -> bool:
        if self.aberto():
            return True
        base = self._base_de().strip().rstrip("/")
        if not endereco_aceitavel(base):
            log.error("the address of the relay is not https, so the hub does not dial it")
            return False
        self._tarefa = asyncio.create_task(self._viver(base))
        # The address only exists after the relay answers, and the caller is the reconciler,
        # which comes back in a moment; nothing here waits on the network.
        return True

    async def fechar(self) -> None:
        tarefa, self._tarefa = self._tarefa, None
        self._url = ""
        if tarefa is not None and not tarefa.done():
            tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa
        for resposta in list(self._respostas):
            resposta.cancel()
        self._respostas.clear()
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def _viver(self, base: str) -> None:
        """Holds the socket open and answers what comes down it, until it is cancelled."""
        sessao = await self._abrir_sessao()
        try:
            async with sessao.ws_connect(
                f"{base}{CAMINHO_DO_HUB}", max_msg_size=QUADRO_MAXIMO, heartbeat=30.0
            ) as socket:
                self._aberto_em = asyncio.get_running_loop().time()
                # The first word is which home this hub belongs to; the relay asks the
                # registry of the maker and answers with an address, or closes.
                await socket.send_str(
                    _texto({"tipo": OLA, "versao": VERSAO, "home": self._home_de()})
                )
                await self._ouvir(socket)
                if socket.close_code == RECUSADO:
                    self._recusado = True
                    log.error("the relay refused this hub: its home is not registered")
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            log.warning("the relay did not answer: %s", erro)
        finally:
            self._url = ""

    async def _ouvir(self, socket) -> None:
        async for mensagem in socket:
            if mensagem.type is not WSMsgType.TEXT:
                return
            try:
                quadro = _json(mensagem.data)
            except ValueError:
                return
            tipo = quadro.get("tipo")
            if tipo == PRONTO:
                url = quadro.get("url")
                self._url = url if isinstance(url, str) else ""
                self._recusado = False
                log.info("remote access: the relay gave this window an address")
            elif tipo == PEDIDO:
                # Each one on its own task: a panel asks for its bundle and its state at the
                # same time, and a queue of one would serve them in single file.
                tarefa = asyncio.create_task(self._responder(socket, quadro))
                self._respostas.add(tarefa)
                tarefa.add_done_callback(self._respostas.discard)

    async def _responder(self, socket, quadro: dict) -> None:
        numero = quadro.get("id")
        try:
            status, cabecalhos, corpo = await self._no_daemon(quadro)
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            log.warning("the local daemon did not answer the relay: %s", erro)
            status, cabecalhos, corpo = 502, {}, b""
        with contextlib.suppress(ConnectionError, RuntimeError, ValueError):
            await socket.send_str(
                _texto(
                    {
                        "tipo": RESPOSTA,
                        "id": numero,
                        "status": status,
                        "cabecalhos": cabecalhos,
                        "corpo": _b64(corpo),
                    }
                )
            )

    async def _no_daemon(self, quadro: dict) -> tuple[int, dict[str, str], bytes]:
        """The request, through the front door of this daemon, on the loopback."""
        caminho = quadro.get("caminho")
        metodo = quadro.get("metodo")
        if not isinstance(caminho, str) or not isinstance(metodo, str):
            raise ValueError("frame without a path")
        cabecalhos = {
            nome: valor
            for nome, valor in (quadro.get("cabecalhos") or {}).items()
            if isinstance(nome, str)
            and isinstance(valor, str)
            and nome.lower() not in CABECALHOS_NOSSOS
        }
        # The authority of the request is the public address of this window, so the gate sees
        # it for what it is: something that came from outside.
        cabecalhos["Host"] = urlsplit(self._url or self._base_de()).netloc
        cabecalhos[CABECALHO_DO_RELAY] = "1"
        sessao = await self._abrir_sessao()
        async with sessao.request(
            metodo,
            f"http://127.0.0.1:{self._porta_local}{caminho}",
            data=_de_b64(quadro.get("corpo")),
            headers=cabecalhos,
            allow_redirects=False,
        ) as resposta:
            bruto = await leitor_de_corpo.inteiro(resposta.content, CORPO_MAXIMO)
            devolver = {
                nome: valor
                for nome, valor in resposta.headers.items()
                if nome.lower() not in CABECALHOS_NOSSOS
            }
            return resposta.status, devolver, bruto

    async def _abrir_sessao(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=PRAZO_S))
            self._sessao = sessao
        return sessao


def _json(bruto: str) -> dict:
    quadro = json.loads(bruto)
    if not isinstance(quadro, dict):
        raise ValueError("frame is not an object")
    return quadro


def _texto(quadro: dict) -> str:
    return json.dumps(quadro, separators=(",", ":"))


def _b64(bruto: bytes) -> str:
    return base64.b64encode(bruto).decode("ascii")


def _de_b64(bruto: object) -> bytes:
    if not isinstance(bruto, str) or not bruto:
        return b""
    return base64.b64decode(bruto, validate=True)
