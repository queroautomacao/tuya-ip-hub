# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The private networks this host sits on, for the panel to prefill the range of the sweep."""

import ipaddress
import socket
from contextlib import suppress
from pathlib import Path

# Networks that belong to the container runtime and never to the customer: the Docker Desktop
# virtual machine and the default docker0 bridge.
REDES_DO_RUNTIME = (
    ipaddress.ip_network("192.168.65.0/24"),
    ipaddress.ip_network("172.17.0.0/16"),
)
# Compose networks usually live here too, so a LAN in this block comes after the others.
BLOCO_172 = ipaddress.ip_network("172.16.0.0/12")
# RFC 1918 only: the documentation and test ranges count as private for ipaddress, and a
# suggestion must never point the sweep at one of those.
BLOCOS_PRIVADOS = (
    ipaddress.ip_network("10.0.0.0/8"),
    BLOCO_172,
    ipaddress.ip_network("192.168.0.0/16"),
)
# A UDP connect never sends a packet; it only makes the kernel pick the outbound interface.
SONDA_DE_ROTA = ("192.0.2.1", 9)
MARCA_DE_CONTAINER = Path("/.dockerenv")


def enderecos_do_host() -> tuple[str, ...]:
    """Every IPv4 address the host answers to its own name, plus the one that routes out."""
    candidatos: list[str] = []
    with suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidatos.append(info[4][0])
    with suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as soquete:
            soquete.connect(SONDA_DE_ROTA)
            candidatos.append(soquete.getsockname()[0])
    return tuple(candidatos)


def redes_locais(
    enderecos: tuple[str, ...] | None = None, *, em_container: bool | None = None
) -> tuple[str, ...]:
    """The /24 of every private address of the host, LAN-looking ones first, runtime ones out.

    Inside a container the whole 172.16/12 block is dropped: a compose network lives there far
    more often than a customer LAN does, and a wrong suggestion costs a sweep that finds nothing.
    """
    if enderecos is None:
        enderecos = enderecos_do_host()
    if em_container is None:
        em_container = MARCA_DE_CONTAINER.exists()
    redes: list[ipaddress.IPv4Network] = []
    for bruto in enderecos:
        try:
            endereco = ipaddress.ip_address(bruto)
        except ValueError:
            continue
        if endereco.version != 4 or not any(endereco in bloco for bloco in BLOCOS_PRIVADOS):
            continue
        rede = ipaddress.ip_network(f"{endereco}/24", strict=False)
        if any(rede.subnet_of(runtime) for runtime in REDES_DO_RUNTIME):
            continue
        if em_container and rede.subnet_of(BLOCO_172):
            continue
        if rede not in redes:
            redes.append(rede)
    redes.sort(key=lambda rede: rede.subnet_of(BLOCO_172))
    return tuple(str(rede) for rede in redes)
