# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Simulated devices: what every driver is tested against, so no test needs hardware.

Section 12: a driver is tested against a fake device, never against a board on a bench.
Each server listens on loopback with port zero, so the test learns the real port and many
instances run side by side. They record what arrived, so a test asserts what the driver
sent, not only what it read back.

Aparelhos simulados: contra o que todo driver é testado, para nenhum teste precisar de
hardware.

Seção 12: um driver é testado contra um aparelho falso, nunca contra uma placa na bancada.
Cada servidor escuta no loopback com porta zero, então o teste descobre a porta real e
várias instâncias rodam lado a lado. Eles guardam o que chegou, para um teste afirmar o
que o driver enviou, não só o que ele leu de volta.
"""

import asyncio
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Self

from aiohttp import web

HOST_LOCAL = "127.0.0.1"
LINHA_MAXIMA = 8 * 1024
DATAGRAMA_MAXIMO = 8 * 1024
BUSCA_TOTAL = "ssdp:all"
PRAZO_PARADA_S = 2.0


class _Servidor:
    """What the three share: the address they ended up on and an async context manager.

    endereco is (host, porta) once iniciar has run, because port zero means the test only
    learns the real port from the server itself.

    O que os três compartilham: o endereço em que pararam e um gerenciador de contexto
    assíncrono.

    endereco é (host, porta) depois que iniciar rodou, porque porta zero significa que o
    teste só descobre a porta real com o próprio servidor.
    """

    endereco: tuple[str, int] = ("", 0)

    async def iniciar(self) -> tuple[str, int]:
        raise NotImplementedError

    async def parar(self) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> Self:
        await self.iniciar()
        return self

    async def __aexit__(self, *_erro: object) -> None:
        await self.parar()


async def _encerrar(tarefas: set[asyncio.Task], espera: Coroutine[Any, Any, None]) -> None:
    """Cancels the open connections first, and gives the whole teardown a deadline.

    Why: since 3.12 wait_closed does not return while a connection is open, so a test that
    left one open would hang forever instead of failing, and a hanging test says nothing.

    Cancela as conexões abertas primeiro, e dá um prazo à parada inteira.

    Por que: desde a 3.12 o wait_closed não retorna enquanto uma conexão está aberta, então
    um teste que deixou uma aberta travaria para sempre em vez de falhar, e teste travado
    não diz nada.
    """
    for tarefa in tarefas:
        tarefa.cancel()
    pendentes = tuple(tarefas)
    tarefas.clear()
    with suppress(TimeoutError):
        async with asyncio.timeout(PRAZO_PARADA_S):
            await asyncio.gather(*pendentes, espera, return_exceptions=True)


class ServidorLinha(_Servidor):
    """TCP speaking one line at a time, the shape of PJLink and of the iEAST port 8899.

    A key of respostas is the line WITHOUT the terminator; the value goes on the wire byte
    for byte, so a test writes the greeting and the terminator it wants. An unknown line is
    recorded and answered with nothing, which is how a real device that ignores a command
    behaves and what makes a timeout testable.

    TCP falando uma linha por vez, o formato do PJLink e da porta 8899 do iEAST.

    A chave de respostas é a linha SEM o terminador; o valor vai para o fio byte a byte,
    então um teste escreve a saudação e o terminador que quiser. Uma linha desconhecida é
    guardada e respondida com nada, que é como se comporta um aparelho real que ignora um
    comando, e o que torna testável um tempo esgotado.
    """

    def __init__(
        self,
        respostas: dict[bytes, bytes],
        *,
        saudacao: bytes = b"",
        terminador: bytes = b"\r",
        atraso_s: float = 0.0,
    ) -> None:
        self.respostas = dict(respostas)
        self.saudacao = saudacao
        self.terminador = terminador
        self.atraso_s = atraso_s
        self.recebidas: list[bytes] = []
        self.conexoes = 0
        self._servidor: asyncio.Server | None = None
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self) -> tuple[str, int]:
        self._servidor = await asyncio.start_server(
            self._atender, HOST_LOCAL, 0, limit=LINHA_MAXIMA
        )
        anfitriao, porta = self._servidor.sockets[0].getsockname()[:2]
        self.endereco = (anfitriao, porta)
        return self.endereco

    async def parar(self) -> None:
        servidor = self._servidor
        if servidor is None:
            return
        self._servidor = None
        servidor.close()
        await _encerrar(self._tarefas, servidor.wait_closed())

    async def _atender(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        tarefa = asyncio.current_task()
        if tarefa is not None:
            self._tarefas.add(tarefa)
        self.conexoes += 1
        try:
            if self.saudacao:
                escritor.write(self.saudacao)
                await escritor.drain()
            while True:
                bruto = await leitor.readuntil(self.terminador)
                linha = bruto[: -len(self.terminador)]
                self.recebidas.append(linha)
                if self.atraso_s:
                    await asyncio.sleep(self.atraso_s)
                resposta = self.respostas.get(linha)
                if resposta:
                    escritor.write(resposta)
                    await escritor.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
            # Why: the driver closing the socket is the normal end of an exchange, and a
            # simulated device that raised on it would fail the test it exists to support.
            # Por que: o driver fechando o socket é o fim normal de uma troca, e um aparelho
            # simulado que estourasse nisso quebraria o teste que ele existe para apoiar.
            pass
        finally:
            if tarefa is not None:
                self._tarefas.discard(tarefa)
            escritor.close()
            with suppress(OSError, asyncio.CancelledError):
                await escritor.wait_closed()


@dataclass(frozen=True)
class Pedido:
    metodo: str
    caminho: str
    corpo: str = ""
    cabecalhos: dict[str, str] = field(default_factory=dict)


class ServidorHttp(_Servidor):
    """HTTP, for the devices that speak it, including the ones that carry the command in the
    query string.

    A key of rotas is matched first against the path with the query string, then against the
    bare path, so both "/httpapi.asp?command=getStatusEx" and "/estado" are one line of test
    setup. An unmapped path answers 404 with an empty body.

    HTTP, para os aparelhos que o falam, inclusive os que levam o comando na query string.

    Uma chave de rotas casa primeiro com o caminho mais a query string, depois com o caminho
    puro, então tanto "/httpapi.asp?command=getStatusEx" quanto "/estado" são uma linha de
    preparo do teste. Um caminho fora do mapa responde 404 com corpo vazio.
    """

    def __init__(self, rotas: dict[str, tuple[int, str]]) -> None:
        self.rotas = dict(rotas)
        self.pedidos: list[Pedido] = []
        self._runner: web.AppRunner | None = None

    async def iniciar(self) -> tuple[str, int]:
        app = web.Application()
        app.router.add_route("*", "/{cauda:.*}", self._atender)
        self._runner = web.AppRunner(app, shutdown_timeout=PRAZO_PARADA_S)
        await self._runner.setup()
        sitio = web.TCPSite(self._runner, HOST_LOCAL, 0)
        await sitio.start()
        anfitriao, porta = self._runner.addresses[0][:2]
        self.endereco = (anfitriao, porta)
        return self.endereco

    async def parar(self) -> None:
        runner = self._runner
        if runner is None:
            return
        self._runner = None
        await _encerrar(set(), runner.cleanup())

    async def _atender(self, request: web.Request) -> web.Response:
        corpo = await request.text()
        self.pedidos.append(
            Pedido(
                metodo=request.method,
                caminho=request.path_qs,
                corpo=corpo,
                cabecalhos=dict(request.headers),
            )
        )
        rota = self.rotas.get(request.path_qs) or self.rotas.get(request.path)
        if rota is None:
            return web.Response(status=404, text="")
        return web.Response(status=rota[0], text=rota[1])


class ServidorDatagrama(_Servidor):
    """UDP answering one datagram per datagram, the shape of a screen or an amplifier relay.

    A key of respostas is the datagram EXACTLY as it arrives, terminator and hexadecimal frame
    included, so a test writes the bytes the driver is supposed to put on the wire. A datagram
    outside the map is recorded and answered with nothing, which is how a device that ignores a
    command behaves and what makes a deadline testable.

    UDP respondendo um datagrama por datagrama, o formato de uma tela ou de um amplificador
    com relé.

    Uma chave de respostas é o datagrama EXATAMENTE como ele chega, terminador e quadro
    hexadecimal inclusos, então um teste escreve os bytes que o driver deveria pôr no fio. Um
    datagrama fora do mapa é guardado e respondido com nada, que é como se comporta um aparelho
    que ignora um comando, e o que torna testável um prazo.
    """

    def __init__(self, respostas: dict[bytes, bytes]) -> None:
        self.respostas = dict(respostas)
        self.recebidos: list[bytes] = []
        self._transporte: asyncio.DatagramTransport | None = None

    async def iniciar(self) -> tuple[str, int]:
        laco = asyncio.get_running_loop()
        transporte, _protocolo = await laco.create_datagram_endpoint(
            lambda: _ProtocoloDatagrama(self), local_addr=(HOST_LOCAL, 0)
        )
        self._transporte = transporte
        anfitriao, porta = transporte.get_extra_info("sockname")[:2]
        self.endereco = (anfitriao, porta)
        return self.endereco

    async def parar(self) -> None:
        if self._transporte is None:
            return
        self._transporte.close()
        self._transporte = None

    def _responder(self, dados: bytes, remetente, transporte: asyncio.DatagramTransport) -> None:
        self.recebidos.append(dados)
        resposta = self.respostas.get(dados)
        if resposta:
            transporte.sendto(resposta, remetente)


class _ProtocoloDatagrama(asyncio.DatagramProtocol):
    def __init__(self, dono: ServidorDatagrama) -> None:
        self._dono = dono
        self._transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transporte = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if self._transporte is not None and len(data) <= DATAGRAMA_MAXIMO:
            self._dono._responder(data, addr, self._transporte)


class RespondedorSsdp(_Servidor):
    """UDP answering an M-SEARCH, one datagram per matching entry of respostas.

    Each entry carries "st", "usn" and "server", and may carry "location". It answers only
    when the ST of the request equals its own or asks for everything, which is what makes a
    plan built from the manifests testable: a search for another ST gets silence.

    UDP respondendo a um M-SEARCH, um datagrama por entrada de respostas que casar.

    Cada entrada leva "st", "usn" e "server", e pode levar "location". Ela só responde
    quando o ST do pedido é igual ao dela ou pede tudo, que é o que torna testável um plano
    montado a partir dos manifestos: uma busca por outro ST recebe silêncio.
    """

    def __init__(self, respostas: tuple[dict, ...]) -> None:
        self.respostas = tuple(respostas)
        self.pedidos: list[bytes] = []
        self._transporte: asyncio.DatagramTransport | None = None

    async def iniciar(self) -> tuple[str, int]:
        laco = asyncio.get_running_loop()
        transporte, _protocolo = await laco.create_datagram_endpoint(
            lambda: _ProtocoloSsdp(self), local_addr=(HOST_LOCAL, 0)
        )
        self._transporte = transporte
        anfitriao, porta = transporte.get_extra_info("sockname")[:2]
        self.endereco = (anfitriao, porta)
        return self.endereco

    async def parar(self) -> None:
        if self._transporte is None:
            return
        self._transporte.close()
        self._transporte = None

    def _responder(self, dados: bytes, remetente, transporte: asyncio.DatagramTransport) -> None:
        self.pedidos.append(dados)
        alvo = _st_do_pedido(dados)
        if alvo is None:
            return
        for resposta in self.respostas:
            if alvo in (BUSCA_TOTAL, resposta.get("st")):
                transporte.sendto(_datagrama(resposta), remetente)


class _ProtocoloSsdp(asyncio.DatagramProtocol):
    def __init__(self, dono: RespondedorSsdp) -> None:
        self._dono = dono
        self._transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transporte = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if self._transporte is not None and len(data) <= DATAGRAMA_MAXIMO:
            self._dono._responder(data, addr, self._transporte)


def _st_do_pedido(dados: bytes) -> str | None:
    texto = dados.decode("utf-8", errors="ignore")
    linhas = texto.split("\r\n")
    if not linhas or not linhas[0].upper().startswith("M-SEARCH "):
        return None
    for linha in linhas[1:]:
        chave, separador, valor = linha.partition(":")
        if separador and chave.strip().upper() == "ST":
            return valor.strip()
    return None


def _datagrama(resposta: dict) -> bytes:
    linhas = [
        "HTTP/1.1 200 OK",
        "CACHE-CONTROL: max-age=1800",
        "EXT:",
        f"ST: {resposta.get('st', '')}",
        f"USN: {resposta.get('usn', '')}",
        f"SERVER: {resposta.get('server', '')}",
    ]
    if resposta.get("location"):
        linhas.append(f"LOCATION: {resposta['location']}")
    return ("\r\n".join([*linhas, "", ""])).encode("utf-8", errors="ignore")
