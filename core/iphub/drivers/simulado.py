# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Simulated devices: what every driver is tested against, so no test needs hardware.

a driver is tested against a fake device, never against a board on a bench.
Each server listens on loopback with port zero, so the test learns the real port and many
instances run side by side. They record what arrived, so a test asserts what the driver
sent, not only what it read back.
"""

import asyncio
import hashlib
import ipaddress
import os
import ssl
import tempfile
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

# Long enough that the second write lands in a segment of its own, short enough that a
# test that reads the whole answer does not feel it.
PAUSA_ENTRE_SEGMENTOS_S = 0.05


class _Servidor:
    """What the three share: the address they ended up on and an async context manager.

    endereco is (host, porta) once iniciar has run, because port zero means the test only
    learns the real port from the server itself.
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

    Since 3.12 wait_closed does not return while a connection is open, so a test that
    left one open would hang forever instead of failing, and a hanging test says nothing.
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
            # The driver closing the socket is the normal end of an exchange, and a
            # simulated device that raised on it would fail the test it exists to support.
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

    With partir set, the body leaves in two writes with a pause between them, which is what a
    device on a busy wifi does and what tells a driver that reads the whole answer from one
    that reads only the first segment.

    With tls set, it listens with a certificate it signs for itself, which is what a device
    that speaks HTTPS on the local network carries: no authority backs it, so a driver that
    verifies the chain fails here exactly as it would on the bench.
    """

    def __init__(
        self, rotas: dict[str, tuple[int, str]], *, partir: bool = False, tls: bool = False
    ) -> None:
        self.rotas = dict(rotas)
        self.partir = partir
        self.tls = tls
        self.pedidos: list[Pedido] = []
        self._runner: web.AppRunner | None = None

    async def iniciar(self) -> tuple[str, int]:
        app = web.Application()
        app.router.add_route("*", "/{cauda:.*}", self._atender)
        self._runner = web.AppRunner(app, shutdown_timeout=PRAZO_PARADA_S)
        await self._runner.setup()
        contexto = await _contexto_proprio() if self.tls else None
        sitio = web.TCPSite(self._runner, HOST_LOCAL, 0, ssl_context=contexto)
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
        if self.partir:
            return await self._em_dois(request, rota)
        return web.Response(status=rota[0], text=rota[1])

    async def _em_dois(self, request: web.Request, rota: tuple[int, str]) -> web.Response:
        estado, texto = rota
        bruto = texto.encode("utf-8")
        meio = max(1, len(bruto) // 2)
        resposta = web.StreamResponse(status=estado)
        resposta.content_length = len(bruto)
        await resposta.prepare(request)
        await resposta.write(bruto[:meio])
        await asyncio.sleep(PAUSA_ENTRE_SEGMENTOS_S)
        await resposta.write(bruto[meio:])
        await resposta.write_eof()
        return resposta


async def _contexto_proprio() -> ssl.SSLContext:
    """A certificate signed by nobody, which is the certificate every device on a LAN has."""
    from iphub.drivers.nativos import android

    certificado, chave = await asyncio.to_thread(android._gerar_certificado)
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    descritor, caminho = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(descritor, "w", encoding="ascii") as arquivo:
            arquivo.write(certificado)
            arquivo.write(chave)
        contexto.load_cert_chain(caminho)
    finally:
        with suppress(OSError):
            os.unlink(caminho)
    return contexto


class ServidorDatagrama(_Servidor):
    """UDP answering one datagram per datagram, the shape of a screen or an amplifier relay.

    A key of respostas is the datagram EXACTLY as it arrives, terminator and hexadecimal frame
    included, so a test writes the bytes the driver is supposed to put on the wire. A datagram
    outside the map is recorded and answered with nothing, which is how a device that ignores a
    command behaves and what makes a deadline testable.
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


# MDNS, RFC 6762, in the shape the SSDP responder above already has: a device that answers a
# one shot query and stays quiet for a service nobody asked for.
SUFIXO_MDNS = ".local"
CABECALHO_DNS = 12
RESPOSTA_DNS = 0x8400
CLASSE_IN = 1
# RFC 6762 sets the cache flush bit on the class of a unique record, and a real speaker
# answers its SRV and its A with it set, so the simulated one does the same.
CLASSE_UNICA = 0x8001
TTL_MDNS = 120
TIPO_A = 1
TIPO_PTR = 12
TIPO_SRV = 33
ROTULO_MAXIMO = 63
REGISTROS_PADRAO = ("ptr", "srv", "a")
PONTEIRO_DNS = 0xC000


class _EscritorDns:
    """A DNS message under construction, with the name compression a real responder uses."""

    def __init__(self) -> None:
        self.dados = bytearray()
        self.posicoes: dict[str, int] = {}

    def nome(self, nome: str) -> None:
        partes = [parte for parte in nome.split(".") if parte]
        while partes:
            atual = ".".join(partes)
            posicao = self.posicoes.get(atual)
            if posicao is not None:
                self.dados += (PONTEIRO_DNS | posicao).to_bytes(2, "big")
                return
            self.posicoes[atual] = len(self.dados)
            bruto = partes[0].encode("utf-8")
            self.dados += bytes([len(bruto)]) + bruto
            partes = partes[1:]
        self.dados += b"\x00"

    def registro_ptr(self, servico: str, instancia: str) -> None:
        marca = self._abrir(servico, TIPO_PTR, CLASSE_IN)
        self.nome(instancia)
        self._fechar(marca)

    def registro_srv(self, instancia: str, porta: int, host: str) -> None:
        marca = self._abrir(instancia, TIPO_SRV, CLASSE_UNICA)
        self.dados += b"\x00\x00\x00\x00" + int(porta).to_bytes(2, "big")
        self.nome(host)
        self._fechar(marca)

    def registro_a(self, host: str, ip: str) -> None:
        marca = self._abrir(host, TIPO_A, CLASSE_UNICA)
        self.dados += ipaddress.IPv4Address(ip).packed
        self._fechar(marca)

    def _abrir(self, nome: str, tipo: int, classe: int) -> int:
        self.nome(nome)
        cabecalho = tipo.to_bytes(2, "big") + classe.to_bytes(2, "big")
        self.dados += cabecalho + TTL_MDNS.to_bytes(4, "big")
        marca = len(self.dados)
        self.dados += b"\x00\x00"
        return marca

    def _fechar(self, marca: int) -> None:
        self.dados[marca : marca + 2] = (len(self.dados) - marca - 2).to_bytes(2, "big")


def quadro_mdns(respostas: tuple[dict, ...], *, pedido: bytes = b"") -> bytes:
    """One mDNS answer: the PTR of each entry, with the SRV and the A in the ADDITIONAL
    section, which is where a real speaker puts them.

    An entry carries "servico", "instancia", "ip" and "porta", and may carry "host" and
    "registros" to say which of ptr, srv and a it sends. pedido is the query being answered:
    its identifier and its question are copied byte for byte, which is what a responder does
    and what keeps every offset a compression pointer refers to.
    """
    escritor = _EscritorDns()
    escritor.dados += b"\x00" * CABECALHO_DNS
    questoes = 0
    pergunta = _questao_mdns(pedido)
    if pergunta is not None:
        nome, _tipo, fim = pergunta
        escritor.dados += pedido[CABECALHO_DNS:fim]
        escritor.posicoes[nome] = CABECALHO_DNS
        questoes = 1
    respondidos = 0
    for entrada in respostas:
        if "ptr" in _quais_mdns(entrada):
            escritor.registro_ptr(_servico_mdns(entrada), _instancia_mdns(entrada))
            respondidos += 1
    adicionais = 0
    for entrada in respostas:
        quais = _quais_mdns(entrada)
        if "srv" in quais:
            porta = int(entrada.get("porta", 80))
            escritor.registro_srv(_instancia_mdns(entrada), porta, _host_mdns(entrada))
            adicionais += 1
        if "a" in quais:
            escritor.registro_a(_host_mdns(entrada), entrada.get("ip", HOST_LOCAL))
            adicionais += 1
    escritor.dados[0:2] = pedido[:2] if len(pedido) >= 2 else b"\x00\x00"
    escritor.dados[2:4] = RESPOSTA_DNS.to_bytes(2, "big")
    escritor.dados[4:6] = questoes.to_bytes(2, "big")
    escritor.dados[6:8] = respondidos.to_bytes(2, "big")
    escritor.dados[10:12] = adicionais.to_bytes(2, "big")
    return bytes(escritor.dados)


class RespondedorMdns(_Servidor):
    """UDP answering a one shot mDNS query, one datagram per matching entry of respostas.

    It answers only the PTR question of its own service, which is what makes a plan built
    from the manifests testable: a query for another service gets silence.
    """

    def __init__(self, respostas: tuple[dict, ...]) -> None:
        self.respostas = tuple(respostas)
        self.pedidos: list[bytes] = []
        self._transporte: asyncio.DatagramTransport | None = None

    async def iniciar(self) -> tuple[str, int]:
        laco = asyncio.get_running_loop()
        transporte, _protocolo = await laco.create_datagram_endpoint(
            lambda: _ProtocoloMdns(self), local_addr=(HOST_LOCAL, 0)
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
        pergunta = _questao_mdns(dados)
        if pergunta is None:
            return
        nome, tipo, _fim = pergunta
        if tipo != TIPO_PTR:
            return
        for resposta in self.respostas:
            if nome == _servico_mdns(resposta):
                transporte.sendto(quadro_mdns((resposta,), pedido=dados), remetente)


class _ProtocoloMdns(asyncio.DatagramProtocol):
    def __init__(self, dono: RespondedorMdns) -> None:
        self._dono = dono
        self._transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transporte = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if self._transporte is not None and len(data) <= DATAGRAMA_MAXIMO:
            self._dono._responder(data, addr, self._transporte)


def _quais_mdns(entrada: dict) -> tuple[str, ...]:
    return tuple(entrada.get("registros", REGISTROS_PADRAO))


def _servico_mdns(entrada: dict) -> str:
    nome = str(entrada.get("servico", "")).strip().strip(".").lower()
    return nome if nome.endswith(SUFIXO_MDNS) else nome + SUFIXO_MDNS


def _instancia_mdns(entrada: dict) -> str:
    return f"{entrada.get('instancia', 'simulado')}.{_servico_mdns(entrada)}"


def _host_mdns(entrada: dict) -> str:
    return str(entrada.get("host", "simulado" + SUFIXO_MDNS)).strip(".").lower()


def _questao_mdns(dados: bytes) -> tuple[str, int, int] | None:
    """The name and the type of the single question of a query, and where it ends."""
    if len(dados) < CABECALHO_DNS:
        return None
    rotulos = []
    posicao = CABECALHO_DNS
    while posicao < len(dados):
        tamanho = dados[posicao]
        posicao += 1
        if tamanho == 0:
            if posicao + 4 > len(dados):
                return None
            tipo = int.from_bytes(dados[posicao : posicao + 2], "big")
            return ".".join(rotulos).lower(), tipo, posicao + 4
        if tamanho > ROTULO_MAXIMO or posicao + tamanho > len(dados):
            return None
        rotulos.append(dados[posicao : posicao + tamanho].decode("utf-8", errors="replace"))
        posicao += tamanho
    return None


class ServidorBytes(_Servidor):
    """TCP with no terminator at all, the shape of the binary line of an amplifier.

    A key of respostas is a request as raw bytes; the server keeps a buffer and, whenever the
    front of it is a known request, consumes it and writes the answer. That is what a device
    of a protocol with no framing does, and it is what lets a test write four questions in one
    call and read four answers back.
    """

    def __init__(self, respostas: dict[bytes, bytes]) -> None:
        self.respostas = dict(respostas)
        self.recebidas: list[bytes] = []
        self._servidor: asyncio.Server | None = None
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self) -> tuple[str, int]:
        self._servidor = await asyncio.start_server(self._atender, HOST_LOCAL, 0)
        self.endereco = self._servidor.sockets[0].getsockname()[:2]
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
        buffer = bytearray()
        try:
            while True:
                pedaco = await leitor.read(1024)
                if not pedaco:
                    return
                buffer += pedaco
                while True:
                    pedido = next(
                        (
                            chave
                            for chave in sorted(self.respostas, key=len, reverse=True)
                            if bytes(buffer).startswith(chave)
                        ),
                        None,
                    )
                    if pedido is None:
                        break
                    del buffer[: len(pedido)]
                    self.recebidas.append(pedido)
                    resposta = self.respostas[pedido]
                    if resposta:
                        escritor.write(resposta)
                        await escritor.drain()
        except (asyncio.IncompleteReadError, OSError):
            return
        finally:
            if tarefa is not None:
                self._tarefas.discard(tarefa)
            escritor.close()
            with suppress(OSError, asyncio.CancelledError):
                await escritor.wait_closed()


class ServidorAndroid(_Servidor):
    """A television speaking the remote protocol of Android: one port to pair, one to command.

    Both ports ask the client for its certificate, which is what the real television does and
    what the whole pairing hangs on, so the test hands its own client certificate here as the
    anchor to trust. The pairing code is COMPUTED from both certificates, exactly as the
    protocol says, so a test that types `codigo` proves the hash of the driver and not a
    string this file made up.
    """

    def __init__(
        self,
        certificado_do_cliente: str,
        *,
        ligada: bool = True,
        volume: tuple[int, int, bool] | None = (18, 24, False),
        recursos: int = 0b1001100011,
        nonce: bytes = b"\x12\x34",
        status_do_pareamento: int = 200,
    ) -> None:
        self.certificado_do_cliente = certificado_do_cliente
        self.ligada = ligada
        self.volume = volume
        self.recursos = recursos
        self.nonce = nonce
        self.status_do_pareamento = status_do_pareamento
        self.recebidas: list[dict] = []
        self.pareada = False
        self.codigo = ""
        self.endereco_pareamento: tuple[str, int] = ("", 0)
        self._escritor_remoto: asyncio.StreamWriter | None = None
        self._chave_pem = ""
        self._certificado_pem = ""
        self._numeros: tuple[int, int] = (0, 0)
        self._servidores: list[asyncio.Server] = []
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self) -> tuple[str, int]:
        from iphub.drivers.nativos import android

        self._certificado_pem, self._chave_pem = await asyncio.to_thread(android._gerar_certificado)
        self._numeros = android._numeros_do_certificado(
            _x509().load_pem_x509_certificate(self._certificado_pem.encode("ascii"))
        )
        pareamento = await asyncio.start_server(
            self._atender_pareamento, HOST_LOCAL, 0, ssl=self._contexto()
        )
        remoto = await asyncio.start_server(
            self._atender_remoto, HOST_LOCAL, 0, ssl=self._contexto()
        )
        self._servidores = [pareamento, remoto]
        self.endereco_pareamento = pareamento.sockets[0].getsockname()[:2]
        self.endereco = remoto.sockets[0].getsockname()[:2]
        return self.endereco

    async def parar(self) -> None:
        servidores, self._servidores = self._servidores, []
        for servidor in servidores:
            servidor.close()
        await _encerrar(
            self._tarefas,
            asyncio.gather(*(s.wait_closed() for s in servidores), return_exceptions=True),
        )

    def _contexto(self) -> "ssl.SSLContext":
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        descritor, caminho = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(descritor, "w", encoding="ascii") as arquivo:
                arquivo.write(self._certificado_pem)
                arquivo.write(self._chave_pem)
            contexto.load_cert_chain(caminho)
        finally:
            with suppress(OSError):
                os.unlink(caminho)
        # The client certificate is self signed, so the only anchor that can verify it is
        # itself; the television verifies nothing and this only has to ASK for the certificate.
        contexto.verify_mode = ssl.CERT_OPTIONAL
        contexto.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        contexto.load_verify_locations(cadata=self.certificado_do_cliente)
        return contexto

    def _guardar(self) -> None:
        tarefa = asyncio.current_task()
        if tarefa is not None:
            self._tarefas.add(tarefa)

    async def _atender_pareamento(
        self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter
    ) -> None:
        from iphub.drivers import protobuf
        from iphub.drivers.nativos import android

        self._guardar()
        self.codigo = self._codigo_de(escritor)
        buffer = bytearray()
        try:
            while True:
                pedaco = await leitor.read(4096)
                if not pedaco:
                    return
                buffer += pedaco
                for corpo in protobuf.quadros(buffer):
                    campos = protobuf.ler(corpo)
                    self.recebidas.append(campos)
                    resposta = self._resposta_do_pareamento(campos)
                    if resposta is None:
                        return
                    cabeca = protobuf.campo_inteiro(
                        android.P_VERSAO, android.VERSAO_DO_PROTOCOLO
                    ) + protobuf.campo_inteiro(android.P_STATUS, self.status_do_pareamento)
                    escritor.write(protobuf.quadro(cabeca + resposta))
                    await escritor.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            return
        finally:
            with suppress(OSError, ssl.SSLError):
                escritor.close()

    def _codigo_de(self, escritor: asyncio.StreamWriter) -> str:
        from iphub.drivers.nativos import android

        objeto = escritor.get_extra_info("ssl_object")
        bruto = objeto.getpeercert(True) if objeto is not None else None
        if not bruto:
            return ""
        cliente = android._numeros_do_certificado(_x509().load_der_x509_certificate(bruto))
        digestor = hashlib.sha256()
        for numero in (cliente[0], cliente[1], self._numeros[0], self._numeros[1]):
            digestor.update(android._em_bytes(numero))
        digestor.update(self.nonce)
        return f"{digestor.digest()[0]:02x}{self.nonce.hex()}"

    def _resposta_do_pareamento(self, campos: dict) -> bytes | None:
        from iphub.drivers import protobuf
        from iphub.drivers.nativos import android

        if protobuf.tem(campos, android.P_PEDIDO):
            return protobuf.campo_mensagem(
                android.P_PEDIDO_ACK, protobuf.campo_texto(1, "simulada")
            )
        if protobuf.tem(campos, android.P_OPCOES):
            codificacao = protobuf.campo_inteiro(
                android.P_TIPO_DE_CODIFICACAO, android.CODIFICACAO_HEXADECIMAL
            ) + protobuf.campo_inteiro(android.P_TAMANHO, android.DIGITOS_DO_CODIGO)
            return protobuf.campo_mensagem(
                android.P_OPCOES, protobuf.campo_mensagem(android.P_ENTRADAS, codificacao)
            )
        if protobuf.tem(campos, android.P_CONFIGURACAO):
            return protobuf.campo_mensagem(android.P_CONFIGURACAO_ACK, b"")
        if protobuf.tem(campos, android.P_SEGREDO):
            self.pareada = True
            segredo = protobuf.crua(protobuf.mensagem(campos, android.P_SEGREDO), 1)
            return protobuf.campo_mensagem(android.P_SEGREDO_ACK, protobuf.campo_bytes(1, segredo))
        return None

    async def _atender_remoto(
        self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter
    ) -> None:
        from iphub.drivers import protobuf
        from iphub.drivers.nativos import android

        self._guardar()
        self._escritor_remoto = escritor
        aparelho = (
            protobuf.campo_texto(android.R_MODELO, "Simulada")
            + protobuf.campo_texto(android.R_FABRICANTE, "Bancada")
            + protobuf.campo_texto(android.R_VERSAO_DO_APP, "1.0")
        )
        configurar = protobuf.campo_inteiro(
            android.R_RECURSOS, self.recursos
        ) + protobuf.campo_mensagem(android.R_APARELHO, aparelho)
        escritor.write(protobuf.quadro(protobuf.campo_mensagem(android.R_CONFIGURAR, configurar)))
        await escritor.drain()
        buffer = bytearray()
        try:
            while True:
                pedaco = await leitor.read(4096)
                if not pedaco:
                    return
                buffer += pedaco
                for corpo in protobuf.quadros(buffer):
                    campos = protobuf.ler(corpo)
                    self.recebidas.append(campos)
                    await self._depois_do_remoto(campos, escritor)
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            return
        finally:
            with suppress(OSError, ssl.SSLError):
                escritor.close()

    async def _depois_do_remoto(self, campos: dict, escritor: asyncio.StreamWriter) -> None:
        from iphub.drivers import protobuf
        from iphub.drivers.nativos import android

        if protobuf.tem(campos, android.R_CONFIGURAR):
            escritor.write(
                protobuf.quadro(
                    protobuf.campo_mensagem(
                        android.R_ATIVAR, protobuf.campo_inteiro(android.R_ATIVO, self.recursos)
                    )
                )
            )
        elif protobuf.tem(campos, android.R_ATIVAR):
            escritor.write(
                protobuf.quadro(
                    protobuf.campo_mensagem(
                        android.R_INICIO, protobuf.campo_bool(android.R_LIGADA, self.ligada)
                    )
                )
            )
            if self.volume is not None:
                nivel, maximo, mudo = self.volume
                corpo = (
                    protobuf.campo_inteiro(android.R_VOLUME_MAXIMO, maximo)
                    + protobuf.campo_inteiro(android.R_VOLUME_NIVEL, nivel)
                    + protobuf.campo_bool(android.R_VOLUME_MUDO, mudo)
                )
                escritor.write(protobuf.quadro(protobuf.campo_mensagem(android.R_VOLUME, corpo)))
        else:
            return
        await escritor.drain()

    async def perguntar_o_ping(self, valor: int = 7) -> None:
        """Sends a ping the way the television does, to see the client answer it."""
        from iphub.drivers import protobuf
        from iphub.drivers.nativos import android

        escritor = self._escritor_remoto
        if escritor is None:
            return
        corpo = protobuf.campo_mensagem(
            android.R_PING, protobuf.campo_inteiro(android.R_VAL1, valor)
        )
        escritor.write(protobuf.quadro(corpo))
        await escritor.drain()


def _x509():
    from cryptography import x509

    return x509
