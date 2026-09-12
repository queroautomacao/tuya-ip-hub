# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The name of the hub on the local network, answered over mDNS by the hub itself.

An installation has no DNS and the address of the hub is whatever the router lent it, so the
integrator would need to read the lease table to open the panel. With this, iphub.local (and
a name of this box alone, iphub-xxxx.local, for a bench with two of them) answers from every
phone and laptop that speaks mDNS, which today is all of them.

Two names and one service, and nothing else: a question for a name this hub carries gets
its address on the interface the question came in by; a question for the panel service gets
the name and the port. Every other question is somebody else's and gets silence, which is
what RFC 6762 asks of a responder that is not the authority for the name.

Silence has one exception, and it is the difference between a name that works and a name
that looks dead: a resolver asks for the address in both families at once, and this hub has
only an IPv4 one. With no answer to the IPv6 question the resolver waits out its timeout,
five seconds on macOS, on EVERY lookup. So a question for a type this hub does not have, on
a name it DOES own, is answered with the NSEC of RFC 6762 section 6.1, which says exactly
which types exist; the resolver stops waiting and the page opens at once.
"""

import asyncio
import logging
import socket
from collections.abc import Sequence

from iphub.drivers.descoberta import ler_nome, nome_em_bytes

log = logging.getLogger("iphub.anuncio")

GRUPO = "224.0.0.251"
PORTA = 5353
DOMINIO = "local"
SERVICO_HTTP = "_http._tcp.local"
SERVICOS_DNS_SD = "_services._dns-sd._udp.local"
TTL_S = 120
NOME_PADRAO = "iphub"

TIPO_A = 1
TIPO_PTR = 12
TIPO_TXT = 16
TIPO_SRV = 33
TIPO_NSEC = 47
TIPO_QUALQUER = 255
CLASSE_IN = 1
# The high bit of the class in a question asks for a unicast answer; in an answer it says
# the record is unique and the cache of the asker may flush the older ones.
BIT_UNICAST = 0x8000
CABECALHO = 12
RESPOSTA_AUTORITATIVA = 0x8400
PERGUNTAS_MAXIMAS = 32


def nomes_de(nome: str, identidade: str) -> tuple[str, str]:
    """The name every hub answers by, and the one of this box alone."""
    base = nome.strip().lower().rstrip(".") or NOME_PADRAO
    return (f"{base}.{DOMINIO}", f"{base}-{identidade}.{DOMINIO}")


def _registro(nome: str, tipo: int, dados: bytes, unico: bool = True) -> bytes:
    codificado = nome_em_bytes(nome)
    if codificado is None:
        return b""
    classe = CLASSE_IN | (BIT_UNICAST if unico else 0)
    return (
        codificado
        + tipo.to_bytes(2, "big")
        + classe.to_bytes(2, "big")
        + TTL_S.to_bytes(4, "big")
        + len(dados).to_bytes(2, "big")
        + dados
    )


def _mapa_de_tipos(tipos: Sequence[int]) -> bytes:
    """The type bit map of an NSEC: which types this name has, in the block of RFC 4034."""
    # Every type this hub answers is under 256, so one window block says all of them.
    maior = max(tipos)
    bits = bytearray(maior // 8 + 1)
    for tipo in tipos:
        bits[tipo // 8] |= 0x80 >> (tipo % 8)
    return bytes([0, len(bits)]) + bytes(bits)


def _nsec(nome: str, tipos: Sequence[int]) -> bytes:
    """The record that says which types this name has, and therefore which it has not."""
    proximo = nome_em_bytes(nome)
    if proximo is None:
        return b""
    return _registro(nome, TIPO_NSEC, proximo + _mapa_de_tipos(tipos))


def _srv(porta: int, alvo: str) -> bytes:
    codificado = nome_em_bytes(alvo) or b"\x00"
    return b"\x00\x00\x00\x00" + porta.to_bytes(2, "big") + codificado


def _instancia(nome_da_caixa: str, servico: str) -> str:
    return f"{nome_da_caixa.removesuffix('.' + DOMINIO)}.{servico}"


def registros(nomes: Sequence[str], ip: str, porta: int, servico: str) -> list[bytes]:
    """Everything this hub says about itself: an address per name, and the panel service."""
    endereco = socket.inet_aton(ip)
    saida = [_registro(nome, TIPO_A, endereco) for nome in nomes]
    instancia = _instancia(nomes[-1], servico)
    saida.append(_registro(servico, TIPO_PTR, nome_em_bytes(instancia) or b"\x00", unico=False))
    saida.append(_registro(instancia, TIPO_SRV, _srv(porta, nomes[-1])))
    saida.append(_registro(instancia, TIPO_TXT, b"\x00"))
    saida.extend(_nsec(nome, (TIPO_A,)) for nome in nomes)
    return [r for r in saida if r]


def _mensagem(respostas: Sequence[bytes], adicionais: Sequence[bytes] = ()) -> bytes:
    return (
        b"\x00\x00"
        + RESPOSTA_AUTORITATIVA.to_bytes(2, "big")
        + b"\x00\x00"
        + len(respostas).to_bytes(2, "big")
        + b"\x00\x00"
        + len(adicionais).to_bytes(2, "big")
        + b"".join(respostas)
        + b"".join(adicionais)
    )


def anuncio(nomes: Sequence[str], ip: str, porta: int, servico: str) -> bytes:
    """The unsolicited announcement this hub sends when it comes up."""
    return _mensagem(registros(nomes, ip, porta, servico))


def perguntas(dados: bytes) -> list[tuple[str, int, bool]] | None:
    """The questions of a query as (name, type, wants unicast), or None for anything else."""
    if len(dados) < CABECALHO or int.from_bytes(dados[2:4], "big") & 0x8000:
        return None
    quantas = int.from_bytes(dados[4:6], "big")
    posicao = CABECALHO
    saida: list[tuple[str, int, bool]] = []
    for _ in range(min(quantas, PERGUNTAS_MAXIMAS)):
        lido = ler_nome(dados, posicao)
        if lido is None or lido[1] + 4 > len(dados):
            return None
        rotulos, posicao = lido
        tipo = int.from_bytes(dados[posicao : posicao + 2], "big")
        classe = int.from_bytes(dados[posicao + 2 : posicao + 4], "big")
        posicao += 4
        nome = ".".join(r.decode("utf-8", errors="ignore") for r in rotulos).lower()
        if classe & 0x7FFF == CLASSE_IN:
            saida.append((nome, tipo, bool(classe & BIT_UNICAST)))
    return saida


def responder(
    dados: bytes, nomes: Sequence[str], ip: str, porta: int, servico: str
) -> tuple[bytes, bool] | None:
    """The answer to a query, and whether it goes back unicast; None when nothing is ours."""
    lidas = perguntas(dados)
    if not lidas:
        return None
    conhecidos = {nome.lower() for nome in nomes}
    respostas: list[bytes] = []
    adicionais: list[bytes] = []
    unicast = False
    for nome, tipo, quer_unicast in lidas:
        if nome in conhecidos and tipo in (TIPO_A, TIPO_QUALQUER):
            respostas.append(_registro(nome, TIPO_A, socket.inet_aton(ip)))
            # Beside the address, what this name does NOT have, so the resolver does not
            # go on to ask for the IPv6 one and wait out its timeout.
            adicionais.append(_nsec(nome, (TIPO_A,)))
        elif nome in conhecidos:
            # A type this hub does not have on a name it owns: the answer is that it does
            # not exist, and not silence, which is five seconds of waiting on every lookup.
            respostas.append(_nsec(nome, (TIPO_A,)))
        elif nome == servico and tipo in (TIPO_PTR, TIPO_QUALQUER):
            respostas.extend(registros(nomes, ip, porta, servico))
        elif nome == SERVICOS_DNS_SD and tipo in (TIPO_PTR, TIPO_QUALQUER):
            respostas.append(
                _registro(SERVICOS_DNS_SD, TIPO_PTR, nome_em_bytes(servico) or b"\x00", unico=False)
            )
        else:
            continue
        unicast = unicast or quer_unicast
    if not respostas:
        return None
    return _mensagem(respostas, [a for a in adicionais if a]), unicast


def ip_para(destino: str) -> str:
    """The address of this host on the interface that reaches the asker."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((destino, PORTA))
        return s.getsockname()[0]


class Anunciante(asyncio.DatagramProtocol):
    """One socket on the mDNS group, answering for the names of this hub."""

    def __init__(self, nomes: Sequence[str], porta: int, servico: str = SERVICO_HTTP) -> None:
        self.nomes = tuple(nomes)
        self._porta = porta
        self._servico = servico
        self._transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transporte) -> None:
        self._transporte = transporte

    def datagram_received(self, dados: bytes, origem) -> None:
        try:
            ip = ip_para(origem[0])
            resposta = responder(dados, self.nomes, ip, self._porta, self._servico)
        except (OSError, ValueError):
            return
        if resposta is None or self._transporte is None:
            return
        mensagem, unicast = resposta
        # A question asked from a phone gets its answer where every phone listens, unless
        # it asked for a private answer; either way the answer never says more than asked.
        self._transporte.sendto(mensagem, origem if unicast else (GRUPO, PORTA))

    def error_received(self, erro) -> None:
        log.debug("mdns socket error: %s", erro)

    def anunciar(self) -> None:
        """Says the names once, unasked, so caches that were waiting learn them now."""
        if self._transporte is None:
            return
        try:
            ip = ip_para(GRUPO)
        except OSError:
            return
        self._transporte.sendto(anuncio(self.nomes, ip, self._porta, self._servico), (GRUPO, PORTA))

    async def iniciar(self) -> bool:
        """Joins the group; False when this host cannot (no multicast, port in use)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", PORTA))
            filiacao = socket.inet_aton(GRUPO) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, filiacao)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setblocking(False)
            await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
        except OSError as erro:
            log.warning("the name of the hub is not announced on mDNS: %s", erro)
            return False
        self.anunciar()
        log.info("answering on mDNS as %s", " and ".join(self.nomes))
        return True

    def parar(self) -> None:
        if self._transporte is not None:
            self._transporte.close()
            self._transporte = None
