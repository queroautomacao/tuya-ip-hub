# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Who is alive at an address and what it is called, asked without a driver.

The sweep of a range used to list only the addresses a driver recognised, and the operator
wants to see the whole segment: every device that answers anything, named as well as the
network allows, with the register button only where a driver exists. Nothing here writes to
a device: a TCP connect closed on success, one SSDP question, one reverse mDNS question, one
NetBIOS status question, one reverse DNS lookup and one GET of the front page, all to the
literal address inside the range the operator named. A device is present when it answered
by itself; a reverse DNS name alone is the resolver talking, and only names.
"""

import asyncio
import html
import logging
import re
import socket
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, replace
from urllib.parse import urlsplit
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.drivers import corpo, descoberta

log = logging.getLogger("iphub.drivers.presenca")

# Ports a home or office device tends to listen on: web, ssh, printing, file sharing, the
# control ports of the drivers of this image, and the ones phones and casting devices open.
PORTAS_TCP = (
    22,
    23,
    53,
    80,
    443,
    445,
    515,
    548,
    554,
    631,
    1255,
    1400,
    1883,
    3000,
    3001,
    4352,
    5000,
    5555,
    6466,
    6467,
    7000,
    8001,
    8002,
    8008,
    8009,
    8060,
    8080,
    8443,
    9100,
    9197,
    49152,
    60128,
    62078,
)
PORTAS_HTTP = (80, 8080)
PORTA_SSDP = 1900
PORTA_MDNS = 5353
PORTA_NETBIOS = 137
TIMEOUT_TCP_S = 0.6
TIMEOUT_UDP_S = 1.0
TIMEOUT_HTTP_S = 1.5
CORPO_MAXIMO = 64 * 1024
PAGINA_MAXIMA = 16 * 1024
TITULO_MAXIMO = 80
NOME_MAXIMO = 120
SUFIXO_REVERSO = ".in-addr.arpa"

_TITULO = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_ESPACOS = re.compile(r"\s+")
# A DTD is what an entity bomb needs, and no device description carries one.
_DTD = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_NOME_NETBIOS = re.compile(r"[A-Za-z0-9._-]{1,15}")
# A NetBIOS node status question for "*": the wildcard name, encoded the NetBIOS way.
PERGUNTA_NETBIOS = (
    b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x20CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\x00\x00\x21\x00\x01"
)
SUFIXOS_NETBIOS_DE_HOST = (0x00, 0x20)
GRUPO_NETBIOS = 0x8000


@dataclass(frozen=True)
class Sondas:
    """Which questions an address gets besides the drivers, and on which ports."""

    tcp: tuple[int, ...] = PORTAS_TCP
    http: tuple[int, ...] = PORTAS_HTTP
    ssdp: int | None = PORTA_SSDP
    mdns: int | None = PORTA_MDNS
    netbios: int | None = PORTA_NETBIOS
    dns: bool = True
    timeout_s: float = TIMEOUT_UDP_S


SONDAS_PADRAO = Sondas()


@dataclass(frozen=True)
class Presenca:
    """What an address answered: open ports, a name and, when SSDP said, maker and model."""

    portas: tuple[int, ...] = ()
    nome: str = ""
    fabricante: str = ""
    modelo: str = ""
    servidor: str = ""
    fontes: tuple[str, ...] = ()

    def descricao(self) -> str:
        marca = f"{self.fabricante} {self.modelo}".strip()
        if marca:
            return marca
        if self.servidor:
            return self.servidor
        if self.portas:
            return "TCP " + ", ".join(str(porta) for porta in self.portas)
        return ""


@dataclass(frozen=True)
class _Anuncio:
    nome: str = ""
    fabricante: str = ""
    modelo: str = ""
    servidor: str = ""
    respondeu: bool = False


async def sondar(
    ip: str, sondas: Sondas = SONDAS_PADRAO, portas_abertas: tuple[int, ...] | None = None
) -> Presenca | None:
    """Everything the address answered, or None when nothing at all did.

    The caller may hand in the open ports it already found: a sweep asks a whole range which
    addresses are alive before it asks the few live ones who they are, and scanning the ports
    twice would double the cost of the expensive half.
    """
    portas, anuncio, reverso, netbios, dns = await asyncio.gather(
        _sabidas(portas_abertas) if portas_abertas is not None else _portas_abertas(ip, sondas.tcp),
        _ssdp(ip, sondas.ssdp, sondas.timeout_s),
        _mdns_reverso(ip, sondas.mdns, sondas.timeout_s),
        _netbios(ip, sondas.netbios, sondas.timeout_s),
        _dns_reverso(ip) if sondas.dns else _nada(),
    )
    if not portas and not anuncio.respondeu and not reverso and not netbios:
        return None
    titulo = ""
    for porta in sondas.http:
        if porta in portas:
            titulo = await _titulo_http(ip, porta)
            if titulo:
                break
    fontes = tuple(
        fonte
        for fonte, veio in (
            ("tcp", bool(portas)),
            ("ssdp", anuncio.respondeu),
            ("mdns", bool(reverso)),
            ("netbios", bool(netbios)),
            ("dns", bool(dns)),
            ("http", bool(titulo)),
        )
        if veio
    )
    nome = anuncio.nome or reverso or netbios or dns or titulo
    return Presenca(
        portas=portas,
        nome=nome[:NOME_MAXIMO],
        fabricante=anuncio.fabricante,
        modelo=anuncio.modelo,
        servidor=anuncio.servidor,
        fontes=fontes,
    )


async def _nada() -> str:
    return ""


async def _sabidas(portas: tuple[int, ...]) -> tuple[int, ...]:
    return portas


async def abertas(
    ip: str, portas: tuple[int, ...], limite: asyncio.Semaphore | None = None
) -> tuple[int, ...]:
    """Which of those ports answer at this address, one connect each, closed on success."""
    return await _portas_abertas(ip, portas, limite)


async def _portas_abertas(
    ip: str, portas: tuple[int, ...], limite: asyncio.Semaphore | None = None
) -> tuple[int, ...]:
    respostas = await asyncio.gather(*(_porta_aberta(ip, porta, limite) for porta in portas))
    return tuple(porta for porta, aberta in zip(portas, respostas, strict=True) if aberta)


async def _porta_aberta(ip: str, porta: int, limite: asyncio.Semaphore | None = None) -> bool:
    # The ceiling is on sockets and not on addresses: a board with four cores drowns in a
    # thousand simultaneous connects, and the sweep it was meant to speed up gets slower.
    try:
        async with AsyncExitStack() as pilha:
            if limite is not None:
                await pilha.enter_async_context(limite)
            await pilha.enter_async_context(asyncio.timeout(TIMEOUT_TCP_S))
            _leitor, escritor = await asyncio.open_connection(ip, porta)
    except (TimeoutError, OSError):
        return False
    escritor.close()
    with suppress(OSError):
        await escritor.wait_closed()
    return True


class _UmaResposta(asyncio.DatagramProtocol):
    """The first datagram the asked host sends back, and nothing from anybody else."""

    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.resposta: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def datagram_received(self, dados: bytes, remetente: tuple) -> None:
        if remetente[0] == self.ip and not self.resposta.done():
            self.resposta.set_result(dados)

    def error_received(self, exc: Exception) -> None:
        if not self.resposta.done():
            self.resposta.set_result(b"")


async def _udp(ip: str, porta: int, pergunta: bytes, timeout_s: float) -> bytes:
    laco = asyncio.get_running_loop()
    try:
        transporte, protocolo = await laco.create_datagram_endpoint(
            lambda: _UmaResposta(ip), local_addr=("0.0.0.0", 0)
        )
    except OSError:
        return b""
    try:
        transporte.sendto(pergunta, (ip, porta))
        async with asyncio.timeout(timeout_s):
            return await protocolo.resposta
    except (TimeoutError, OSError):
        return b""
    finally:
        transporte.close()


async def _ssdp(ip: str, porta: int | None, timeout_s: float) -> _Anuncio:
    if porta is None:
        return _Anuncio()
    pergunta = (
        f'M-SEARCH * HTTP/1.1\r\nHOST: {ip}:{porta}\r\nMAN: "ssdp:discover"\r\n'
        "MX: 1\r\nST: ssdp:all\r\n\r\n"
    ).encode("ascii")
    resposta = await _udp(ip, porta, pergunta, timeout_s)
    if not resposta:
        return _Anuncio()
    cabecalhos = _cabecalhos(resposta)
    anuncio = _Anuncio(servidor=cabecalhos.get("server", "")[:NOME_MAXIMO], respondeu=True)
    local = cabecalhos.get("location", "")
    # The description is fetched only from the host that announced it, over http, so an
    # announcement can never send this hub knocking on a third address.
    if _e_do_proprio(local, ip):
        anuncio = await _descricao_upnp(local, anuncio)
    return anuncio


def _cabecalhos(bruto: bytes) -> dict[str, str]:
    cabecalhos: dict[str, str] = {}
    for linha in bruto.decode("latin-1").split("\r\n")[1:]:
        nome, separador, valor = linha.partition(":")
        if separador and nome.strip():
            cabecalhos.setdefault(nome.strip().lower(), valor.strip())
    return cabecalhos


def _e_do_proprio(url: str, ip: str) -> bool:
    try:
        partes = urlsplit(url)
    except ValueError:
        return False
    return partes.scheme == "http" and partes.hostname == ip


async def _descricao_upnp(url: str, anuncio: _Anuncio) -> _Anuncio:
    bruto = await _pagina(url, CORPO_MAXIMO)
    if not bruto or _DTD.search(bruto):
        return anuncio
    try:
        raiz = ElementTree.fromstring(bruto)
    except ElementTree.ParseError:
        return anuncio
    campos = {"friendlyName": "", "manufacturer": "", "modelName": ""}
    for elemento in raiz.iter():
        chave = elemento.tag.rsplit("}", 1)[-1]
        if chave in campos and not campos[chave] and elemento.text:
            campos[chave] = _ESPACOS.sub(" ", elemento.text).strip()[:NOME_MAXIMO]
    return replace(
        anuncio,
        nome=campos["friendlyName"],
        fabricante=campos["manufacturer"],
        modelo=campos["modelName"],
    )


async def _pagina(url: str, maximo: int) -> bytes:
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=TIMEOUT_HTTP_S)) as sessao,
            sessao.get(url, allow_redirects=False) as resposta,
        ):
            if resposta.status != 200:
                return b""
            return await corpo.inteiro(resposta.content, maximo)
    except (ClientError, OSError, TimeoutError, ValueError):
        return b""


def _nome_reverso(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + SUFIXO_REVERSO


async def _mdns_reverso(ip: str, porta: int | None, timeout_s: float) -> str:
    """The.local name of the host, asked to the host itself as a legacy unicast query."""
    if porta is None:
        return ""
    reverso = _nome_reverso(ip)
    pergunta = descoberta._pergunta_mdns(reverso)
    if pergunta is None:
        return ""
    resposta = await _udp(ip, porta, pergunta, timeout_s)
    registros = descoberta._registros(resposta) if resposta else None
    if not registros:
        return ""
    esperado = reverso.lower()
    for nome, tipo, inicio, _tamanho in registros:
        if tipo != descoberta.TIPO_PTR or descoberta._texto_do_nome(nome) != esperado:
            continue
        lido = descoberta._nome(resposta, inicio)
        if lido is None:
            continue
        texto = ".".join(descoberta._texto_do_rotulo(r) for r in lido[0]).strip(".")
        if texto:
            return texto[:NOME_MAXIMO]
    return ""


async def _netbios(ip: str, porta: int | None, timeout_s: float) -> str:
    if porta is None:
        return ""
    return nome_netbios(await _udp(ip, porta, PERGUNTA_NETBIOS, timeout_s))


def nome_netbios(resposta: bytes) -> str:
    """The host name inside a node status answer, or empty when there is none."""
    posicao = 12
    if len(resposta) <= posicao:
        return ""
    if resposta[posicao] & 0xC0 == 0xC0:
        posicao += 2
    else:
        while posicao < len(resposta) and resposta[posicao] != 0:
            posicao += 1 + resposta[posicao]
        posicao += 1
    posicao += 2 + 2 + 4 + 2
    if posicao >= len(resposta):
        return ""
    quantos = resposta[posicao]
    posicao += 1
    for _ in range(quantos):
        entrada = resposta[posicao : posicao + 18]
        if len(entrada) < 18:
            break
        posicao += 18
        nome = entrada[:15].decode("ascii", errors="replace").strip()
        sufixo = entrada[15]
        bandeiras = int.from_bytes(entrada[16:18], "big")
        if bandeiras & GRUPO_NETBIOS or sufixo not in SUFIXOS_NETBIOS_DE_HOST:
            continue
        if _NOME_NETBIOS.fullmatch(nome):
            return nome
    return ""


ARQUIVO_ARP = "/proc/net/arp"
_MAC_VAZIO = "00:00:00:00:00:00"


def tabela_arp(caminho: str = ARQUIVO_ARP) -> dict[str, str]:
    """The addresses the kernel resolved to a MAC, which on a Linux host with the LAN interface
    is every device the TCP probes touched, open port or not.

    A Wi-Fi appliance with no open port answers nothing above the link layer, but the
    connect attempt still made the kernel ask who owns the address, and the answer stays in
    this table. Inside Docker Desktop the container has no LAN interface and the table is
    empty, which is the honest answer there.
    """
    try:
        with open(caminho, encoding="ascii", errors="replace") as arquivo:
            return ler_arp(arquivo.read())
    except OSError:
        return {}


def ler_arp(texto: str) -> dict[str, str]:
    """The ip to MAC pairs of a /proc/net/arp listing, skipping the unresolved ones."""
    pares: dict[str, str] = {}
    for linha in texto.splitlines()[1:]:
        campos = linha.split()
        if len(campos) < 4:
            continue
        ip, mac = campos[0], campos[3].lower()
        if len(mac) == 17 and mac != _MAC_VAZIO and int(campos[2], 16) & 0x2:
            pares[ip] = mac
    return pares


async def _dns_reverso(ip: str) -> str:
    laco = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(TIMEOUT_UDP_S):
            nome, _porta = await laco.getnameinfo((ip, 0), socket.NI_NAMEREQD)
    except (TimeoutError, OSError, ValueError):
        return ""
    return "" if nome == ip else nome[:NOME_MAXIMO]


async def _titulo_http(ip: str, porta: int) -> str:
    bruto = await _pagina(f"http://{ip}:{porta}/", PAGINA_MAXIMA)
    achado = _TITULO.search(bruto) if bruto else None
    if achado is None:
        return ""
    texto = html.unescape(achado.group(1).decode("utf-8", errors="replace"))
    return _ESPACOS.sub(" ", texto).strip()[:TITULO_MAXIMO]
