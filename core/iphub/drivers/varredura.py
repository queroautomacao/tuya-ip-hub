# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The sweep of a range the operator named, one identificar per address.

The discovery is generated from the manifests and travels by multicast,
and multicast does not cross every network this hub runs on (on Docker Desktop
of macOS it crosses none). Where it does not, the operator was left typing an address and an
identity by hand, which is two chances to mistype what the device already knows about
itself. So a range is swept by asking each address the one question already
defines without a registration: identificar. Nothing here writes to a device.
"""

import asyncio
import ipaddress
import logging
from collections.abc import Mapping

from iphub.drivers import presenca
from iphub.drivers.base import Driver
from iphub.drivers.descoberta import Achado

log = logging.getLogger("iphub.drivers.varredura")

# A range is a promise to open a socket to every address in it, so the ceiling is what
# keeps a typo of one character (a /8 instead of a /24) from becoming sixteen million
# connections leaving this host. A /22 is four home networks and more than any installation
# this product is for.
ENDERECOS_MAXIMOS = 1024

# The nine drivers of an address are asked AT ONCE, so an address with nothing on it
# costs one timeout and not nine in a row. Asked one after the other, a /24 needed a hundred
# and forty seconds and the sweep died on its own ceiling with an empty answer, which is the
# worst thing it could report. Measured on the bench: nine drivers in parallel settle an
# address in the two seconds of the timeout, and a receiver that is there names itself in
# seventy milliseconds.
TIMEOUT_PADRAO = 2.0

# The ceiling counts ADDRESSES asked WHO THEY ARE, which is the expensive half: a driver
# probe each, an SSDP question, an mDNS question, a NetBIOS question, a reverse lookup and a
# page fetch. Only the addresses that answered a port get there, so the ceiling is low.
SIMULTANEAS_PADRAO = 16

# Sockets in flight during the liveness pass, where every address of the range is asked at
# once. What is capped is the number of open connects and not the number of addresses,
# because a board with four cores drowns in a thousand simultaneous ones.
CONEXOES_SIMULTANEAS = 256

FAIXA_INVALIDA = "faixa_invalida"
FAIXA_PUBLICA = "faixa_publica"
FAIXA_GRANDE = "faixa_grande"


class FaixaRecusada(ValueError):
    """A range this hub refuses to sweep, with the stable code that says why."""

    def __init__(self, codigo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.codigo = codigo


def ler_faixa(bruto: object) -> ipaddress.IPv4Network:
    """The range as a network, or FaixaRecusada saying which rule it broke.

    A public range is somebody pointing this hub at the internet, by mistake or not,
    and the answer is no before the first socket and not a log line after the last. The three
    codes are separate because "you typed it wrong" and "that is not your network" send the
    operator to different places.
    """
    if not isinstance(bruto, str) or not bruto.strip():
        raise FaixaRecusada(FAIXA_INVALIDA, "a range is a string in CIDR form")
    try:
        rede = ipaddress.ip_network(bruto.strip(), strict=False)
    except ValueError as erro:
        raise FaixaRecusada(FAIXA_INVALIDA, f"{bruto!r} is not a network") from erro
    if not isinstance(rede, ipaddress.IPv4Network):
        raise FaixaRecusada(FAIXA_INVALIDA, "only IPv4 ranges are swept")
    if not (rede.is_private or rede.is_link_local):
        raise FaixaRecusada(FAIXA_PUBLICA, f"{rede} is not a private range")
    if rede.num_addresses > ENDERECOS_MAXIMOS:
        raise FaixaRecusada(
            FAIXA_GRANDE,
            f"{rede} has {rede.num_addresses} addresses, the ceiling is {ENDERECOS_MAXIMOS}",
        )
    return rede


def enderecos(rede: ipaddress.IPv4Network) -> tuple[str, ...]:
    """The addresses to knock on: the hosts of the network, and the address itself on a /32."""
    if rede.prefixlen >= 31:
        return tuple(str(endereco) for endereco in rede)
    return tuple(str(endereco) for endereco in rede.hosts())


async def procurar(
    rede: ipaddress.IPv4Network,
    classes: Mapping[str, type[Driver]],
    *,
    timeout_s: float = TIMEOUT_PADRAO,
    simultaneas: int = SIMULTANEAS_PADRAO,
    sondas: presenca.Sondas | None = None,
) -> tuple[Achado, ...]:
    """Every address of the range asked who lives there, by every driver that can ask.

    Every driver of an address is asked at once and the answers are read in the order of
    the catalogue, which is alphabetical and therefore the same on every hub. Asking them all
    is what makes a dead address cost one timeout instead of nine; reading them in a fixed
    order is what keeps a segment from answering two different types for one address
    depending on which coroutine woke first.
    """
    perguntam = tuple((tipo, classes[tipo]) for tipo in sorted(classes) if _pergunta(classes[tipo]))
    if not perguntam and sondas is None:
        return ()
    vaga = asyncio.Semaphore(max(1, simultaneas))

    async def presente(ip: str, portas: tuple[int, ...]) -> presenca.Presenca | None:
        # An address is listed even when no driver knows it, so the operator sees the
        # whole segment and not only what this image can register.
        if sondas is None:
            return None
        try:
            return await presenca.sondar(ip, sondas, portas)
        except Exception as erro:
            log.debug("%s could not be probed: %s", ip, erro)
            return None

    async def perguntar(ip: str, tipo: str, classe: type[Driver]) -> str:
        try:
            async with asyncio.timeout(timeout_s):
                return await classe.identificar(ip) or ""
        except (TimeoutError, asyncio.CancelledError):
            return ""
        except Exception as erro:
            log.debug("%s did not answer %s: %s", ip, tipo, erro)
            return ""

    async def um(ip: str, portas: tuple[int, ...]) -> Achado | None:
        async with vaga:
            nomes, vivo = await asyncio.gather(
                asyncio.gather(*(perguntar(ip, tipo, classe) for tipo, classe in perguntam)),
                presente(ip, portas),
            )
        descricao = vivo.descricao() if vivo is not None else ""
        nome = vivo.nome if vivo is not None else ""
        for (tipo, _), identidade in zip(perguntam, nomes, strict=True):
            if identidade:
                return Achado(
                    tipo=tipo,
                    identidade=identidade,
                    ip=ip,
                    porta=None,
                    descricao=descricao,
                    nome=nome,
                )
        if vivo is not None:
            return Achado(tipo="", identidade="", ip=ip, porta=None, descricao=descricao, nome=nome)
        return None

    alvos = enderecos(rede)
    # Two passes, because asking an address who it is costs a probe per driver, an SSDP
    # question, an mDNS question, a NetBIOS question, a reverse lookup and a page fetch, and a
    # range is mostly empty. The first pass is one connect per port and says which addresses
    # are alive; only those are asked who they are. Every port a driver of this image speaks
    # is in that list, so nothing that could be registered is lost.
    vivos = await _vivos(alvos, sondas)
    # The probes above made the kernel resolve every address they touched, so a device
    # that opened no port is still in the ARP table of a host that owns the LAN interface.
    arp = presenca.tabela_arp() if sondas is not None else {}
    candidatos = tuple(ip for ip in alvos if ip in vivos or ip in arp)
    achados = await asyncio.gather(*(um(ip, vivos.get(ip, ())) for ip in candidatos))
    resultado = []
    for ip, achado in zip(candidatos, achados, strict=True):
        if achado is not None:
            resultado.append(achado)
        elif ip in arp:
            resultado.append(
                Achado(tipo="", identidade="", ip=ip, porta=None, descricao=f"MAC {arp[ip]}")
            )
    return tuple(resultado)


async def _vivos(
    alvos: tuple[str, ...], sondas: presenca.Sondas | None
) -> dict[str, tuple[int, ...]]:
    """The addresses that answered a port, and which ports. With no probes every address is a
    candidate, because then the drivers themselves are the only question there is."""
    if sondas is None or not sondas.tcp:
        return dict.fromkeys(alvos, ())
    limite = asyncio.Semaphore(CONEXOES_SIMULTANEAS)

    async def um(ip: str) -> tuple[str, tuple[int, ...]]:
        try:
            return ip, await presenca.abertas(ip, sondas.tcp, limite)
        except OSError as erro:
            log.debug("%s could not be reached: %s", ip, erro)
            return ip, ()

    pares = await asyncio.gather(*(um(ip) for ip in alvos))
    return {ip: portas for ip, portas in pares if portas}


def _pergunta(classe: type[Driver]) -> bool:
    """Whether that driver overrides identificar, which is what makes it sweepable.

    The one of the base answers None to everything, so asking it once per address of a
    range is a thousand awaits that can only fail. A cloud driver has nothing on the segment
    and never overrides it, which is why the same check keeps it out.
    """
    return classe.identificar.__func__ is not Driver.identificar.__func__
