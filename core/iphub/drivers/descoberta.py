# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Discovery generated from the manifests: an SSDP sweep that believes only the sender.

Section 6: the plan is built from the manifests, never written by hand, and two manifests
that claim the same signature are a test error, not a decision taken in runtime. Section 9
in spirit: an answer is data, so the address of a device is the address the datagram came
from and never what the answer points at.

mDNS: the plan carries the services the manifests declare, and procurar does NOT search
them. The mDNS transport arrives with the driver that needs it (LinkPlay, milestone 4).

Descoberta gerada dos manifestos: uma varredura SSDP que acredita apenas no remetente.

Seção 6: o plano nasce dos manifestos, nunca escrito à mão, e dois manifestos que
reivindicam a mesma assinatura são erro de teste, não decisão tomada em runtime. Seção 9
em espírito: uma resposta é dado, então o endereço de um aparelho é o endereço de onde o
datagrama veio e nunca o que a resposta aponta.

mDNS: o plano carrega os serviços que os manifestos declaram, e procurar NÃO os busca. O
transporte mDNS chega com o driver que precisar dele (LinkPlay, marco 4).
"""

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from iphub.drivers.manifesto import Manifesto

log = logging.getLogger("iphub.drivers.descoberta")

DESTINO_PADRAO = ("239.255.255.250", 1900)
BIND_PADRAO = ("0.0.0.0", 0)
TIMEOUT_PADRAO = 3.0
BUSCA_TOTAL = "ssdp:all"

DESCRICAO_MAXIMA = 200
DATAGRAMA_MAXIMO = 8 * 1024
BUFFER_RECEBIMENTO = 64 * 1024
ERROS_ATE_DESISTIR = 5
MX_MINIMO = 1
MX_MAXIMO = 5

# Why: one device answering in a loop on a customer LAN must not grow the answer nor the
# memory of this daemon; one ceiling bounds what a sweep finds, the other the work it does.
# Por que: um aparelho respondendo em laço na LAN do cliente não pode crescer a resposta nem
# a memória deste daemon; um teto limita o que a varredura acha, o outro o trabalho que faz.
ACHADOS_MAXIMOS = 200
DATAGRAMAS_MAXIMOS = 5000

# Why: the uuid stops at the two colons that separate it from the service inside a USN, so
# the colon is out of the class on purpose.
# Por que: o uuid termina nos dois pontos que o separam do serviço dentro de um USN, então
# o dois pontos fica fora da classe de propósito.
_UUID = re.compile(r"uuid:([0-9A-Za-z][0-9A-Za-z._-]{0,127})")


@dataclass(frozen=True)
class Achado:
    """One device seen on the segment; tipo and identidade are empty when unknown.

    Um aparelho visto no segmento; tipo e identidade ficam vazios quando desconhecidos.
    """

    tipo: str
    identidade: str
    ip: str
    porta: int | None
    descricao: str


class PlanoAmbiguo(ValueError):
    """Two types claim the same signature, which the suite catches before any hub boots.

    Dois tipos reivindicam a mesma assinatura, que a suíte pega antes de qualquer hub subir.
    """


@dataclass(frozen=True)
class Plano:
    """What to search and how to read the answer, generated from the manifests.

    mdns lists the services the manifests declare and nothing searches them yet; the
    transport arrives with the driver that needs it (LinkPlay, milestone 4).

    O que buscar e como ler a resposta, gerado a partir dos manifestos.

    mdns lista os serviços que os manifestos declaram e nada os busca ainda; o transporte
    chega com o driver que precisar dele (LinkPlay, marco 4).
    """

    sts: tuple[str, ...] = ()
    por_st: dict[str, str] = field(default_factory=dict)
    fabricantes: tuple[tuple[str, str], ...] = ()
    mdns: dict[str, str] = field(default_factory=dict)


def montar(manifestos: Iterable[Manifesto]) -> Plano:
    """The plan of every manifest handed in, refusing a signature two types claim.

    O plano de todos os manifestos entregues, recusando uma assinatura que dois tipos pedem.
    """
    por_st: dict[str, str] = {}
    fabricantes: dict[str, str] = {}
    mdns: dict[str, str] = {}
    conflitos: list[str] = []
    for manifesto in manifestos:
        descoberta = manifesto.descoberta
        for assinatura in descoberta.ssdp_st:
            _reivindicar(por_st, assinatura, manifesto.tipo, "ssdp_st", conflitos)
        for fabricante in descoberta.ssdp_fabricantes:
            _reivindicar(
                fabricantes,
                fabricante.strip().lower(),
                manifesto.tipo,
                "ssdp_fabricantes",
                conflitos,
            )
        for servico in descoberta.mdns_servicos:
            _reivindicar(mdns, servico.strip(), manifesto.tipo, "mdns_servicos", conflitos)
    if conflitos:
        raise PlanoAmbiguo("ambiguous discovery plan: " + "; ".join(sorted(conflitos)))
    return Plano(
        sts=tuple(sorted(por_st)),
        por_st=dict(sorted(por_st.items())),
        fabricantes=tuple(sorted(fabricantes.items())),
        mdns=dict(sorted(mdns.items())),
    )


async def procurar(
    plano: Plano,
    *,
    destino: tuple[str, int] = DESTINO_PADRAO,
    timeout_s: float = TIMEOUT_PADRAO,
    bind: tuple[str, int] = BIND_PADRAO,
) -> tuple[Achado, ...]:
    """One M-SEARCH per target, then every answer that arrives before timeout_s, folded.

    Um M-SEARCH por alvo, depois toda resposta que chegar antes de timeout_s, dobrada.
    """
    alvos = _alvos(plano)
    if not alvos:
        return ()
    laco = asyncio.get_running_loop()
    soquete = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        soquete.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Why: a device flooding the group must not grow the kernel queue while the sweep
        # runs; what does not fit in the cap is dropped, and a sweep is repeatable.
        # Por que: um aparelho inundando o grupo não pode crescer a fila do núcleo enquanto a
        # varredura roda; o que não couber no teto é descartado, e uma varredura se repete.
        with suppress(OSError):
            soquete.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUFFER_RECEBIMENTO)
        soquete.setblocking(False)
        soquete.bind(bind)
        for alvo in alvos:
            await laco.sock_sendto(soquete, _msearch(alvo, destino, timeout_s), destino)
        return await _coletar(laco, soquete, plano, timeout_s)
    finally:
        soquete.close()


def _reivindicar(
    mapa: dict[str, str], chave: str, tipo: str, campo: str, conflitos: list[str]
) -> None:
    # Why: an empty substring would match every answer on the segment, so it claims nothing.
    # Por que: um trecho vazio casaria com toda resposta do segmento, então não reivindica nada.
    if not chave:
        return
    dono = mapa.setdefault(chave, tipo)
    if dono != tipo:
        conflitos.append(f"{campo} {chave!r} is claimed by {dono!r} and by {tipo!r}")


def _alvos(plano: Plano) -> tuple[str, ...]:
    """Every declared ST, plus the search for everything when a manufacturer is declared.

    Todo ST declarado, mais a busca por tudo quando um fabricante é declarado.
    """
    # Why: a manufacturer substring is not something to ask for, it only reads an answer that
    # already arrived, so a plan that carries one has to ask the segment for everything.
    # Por que: um trecho de fabricante não é algo a pedir, ele só lê uma resposta que já
    # chegou, então um plano que carrega um precisa pedir tudo ao segmento.
    alvos = list(plano.sts)
    if plano.fabricantes and BUSCA_TOTAL not in alvos:
        alvos.append(BUSCA_TOTAL)
    return tuple(alvos)


def _mx(timeout_s: float) -> int:
    return max(MX_MINIMO, min(MX_MAXIMO, int(timeout_s)))


def _cabecalho_seguro(valor: str) -> str:
    # Why: a manifest is data on disk, and a carriage return inside an ST would write a
    # header of its own into the datagram this daemon puts on the wire.
    # Por que: um manifesto é dado em disco, e um retorno de carro dentro de um ST escreveria
    # um cabeçalho próprio no datagrama que este daemon põe no fio.
    return "".join(c for c in valor if c.isprintable())


def _msearch(alvo: str, destino: tuple[str, int], timeout_s: float) -> bytes:
    linhas = (
        "M-SEARCH * HTTP/1.1",
        f"HOST: {destino[0]}:{destino[1]}",
        'MAN: "ssdp:discover"',
        f"MX: {_mx(timeout_s)}",
        f"ST: {_cabecalho_seguro(alvo)}",
        "",
        "",
    )
    return "\r\n".join(linhas).encode("ascii", errors="ignore")


async def _coletar(
    laco: asyncio.AbstractEventLoop, soquete: socket.socket, plano: Plano, timeout_s: float
) -> tuple[Achado, ...]:
    coleta = _Coleta()
    erros = 0
    datagramas = 0
    fim = laco.time() + timeout_s
    while erros < ERROS_ATE_DESISTIR:
        restante = fim - laco.time()
        if restante <= 0:
            break
        try:
            dados, remetente = await asyncio.wait_for(
                laco.sock_recvfrom(soquete, DATAGRAMA_MAXIMO), restante
            )
        except TimeoutError:
            break
        except OSError:
            # Why: an unreachable host on the segment surfaces on the next receive, and it is
            # not a reason to abandon a sweep that other devices are still answering.
            # Por que: um host inalcançável no segmento aparece na leitura seguinte, e não é
            # razão para abandonar uma varredura que outros aparelhos ainda respondem.
            erros += 1
            continue
        erros = 0
        datagramas += 1
        achado = _ler(dados, remetente[0], plano)
        if achado is not None:
            coleta.guardar(achado)
        if len(coleta.por_chave) >= ACHADOS_MAXIMOS:
            teto = f"ACHADOS_MAXIMOS ({ACHADOS_MAXIMOS})"
        elif datagramas >= DATAGRAMAS_MAXIMOS:
            teto = f"DATAGRAMAS_MAXIMOS ({DATAGRAMAS_MAXIMOS})"
        else:
            continue
        # Why: a sweep cut short leaves a device off the list, and the reason has to be in
        # the log. Por que: uma varredura cortada deixa aparelho fora, e a razão vai ao log.
        log.warning("discovery sweep cut short by %s; a later answer is missing from it", teto)
        break
    return coleta.resultado()


def _ler(dados: bytes, ip: str, plano: Plano) -> Achado | None:
    cabecalhos = _cabecalhos(dados)
    if cabecalhos is None:
        return None
    servidor = cabecalhos.get("SERVER", "")
    usn = cabecalhos.get("USN", "")
    uuid = _UUID.search(usn)
    return Achado(
        tipo=_tipo(plano, cabecalhos.get("ST", ""), servidor, usn),
        identidade=uuid.group(1) if uuid else "",
        ip=ip,
        porta=_porta(cabecalhos.get("LOCATION", ""), ip),
        descricao=_texto_limpo(servidor),
    )


def _cabecalhos(dados: bytes) -> dict[str, str] | None:
    """The headers of an SSDP answer, or None for a datagram that is not one.

    Os cabeçalhos de uma resposta SSDP, ou None para um datagrama que não é uma.
    """
    texto = dados[:DATAGRAMA_MAXIMO].decode("utf-8", errors="ignore")
    linhas = texto.replace("\r\n", "\n").split("\n")
    partes = linhas[0].split()
    if len(partes) < 2 or not partes[0].upper().startswith("HTTP/1.") or partes[1] != "200":
        return None
    cabecalhos: dict[str, str] = {}
    for linha in linhas[1:]:
        # Why: the empty line ends the headers, as HTTP defines, so a line of the body shaped
        # like a header cannot beat the header the device really sent.
        # Por que: a linha vazia encerra os cabeçalhos, como o HTTP define, então uma linha do
        # corpo com forma de cabeçalho não pode vencer o cabeçalho que o aparelho mandou.
        if not linha.strip():
            break
        chave, separador, valor = linha.partition(":")
        if separador:
            # Why: the first occurrence wins, so a header repeated later in the same datagram
            # cannot overwrite the one already read.
            # Por que: a primeira ocorrência vence, então um cabeçalho repetido mais adiante no
            # mesmo datagrama não sobrescreve o que já foi lido.
            cabecalhos.setdefault(chave.strip().upper(), valor.strip())
    return cabecalhos


def _tipo(plano: Plano, st: str, servidor: str, usn: str) -> str:
    tipo = plano.por_st.get(st)
    if tipo:
        return tipo
    alvo = f"{servidor}\n{usn}".lower()
    for fragmento, tipo_do_fabricante in plano.fabricantes:
        if fragmento in alvo:
            return tipo_do_fabricante
    return ""


def _porta(location: str, ip: str) -> int | None:
    """The port of LOCATION, and only when LOCATION names the sender itself.

    A porta do LOCATION, e só quando o LOCATION nomeia o próprio remetente.
    """
    # Why: the port is a hint for the registration form, and an answer that names another
    # host is pointing somewhere else, which is exactly what must not be followed.
    # Por que: a porta é uma dica para o cadastro, e uma resposta que nomeia outro host está
    # apontando para outro lugar, que é exatamente o que não se pode seguir.
    if not location:
        return None
    try:
        partes = urlsplit(location.strip())
        anfitriao, porta = partes.hostname, partes.port
    except ValueError:
        return None
    if porta is None or anfitriao is None:
        return None
    try:
        mesmo_aparelho = ipaddress.ip_address(anfitriao) == ipaddress.ip_address(ip)
    except ValueError:
        return None
    return porta if mesmo_aparelho else None


def _texto_limpo(bruto: str) -> str:
    return "".join(c for c in bruto if c.isprintable())[:DESCRICAO_MAXIMA].strip()


def _mesclar(antigo: Achado, novo: Achado) -> Achado:
    """Keeps the answer that names a tipo and completes it with what the other one carried.

    Mantém a resposta que nomeia um tipo e a completa com o que a outra carregava.
    """
    base, extra = (antigo, novo) if antigo.tipo else (novo, antigo)
    return replace(
        base,
        identidade=base.identidade or extra.identidade,
        porta=base.porta if base.porta is not None else extra.porta,
        descricao=base.descricao or extra.descricao,
    )


@dataclass
class _Coleta:
    """The answer of a sweep while it is built: folded on arrival, one entry per device.

    A resposta de uma varredura enquanto nasce: dobrada na chegada, uma entrada por aparelho.
    """

    por_chave: dict[tuple[str, str, str], Achado] = field(default_factory=dict)
    ips_por_identidade: dict[str, set[str]] = field(default_factory=dict)

    def guardar(self, achado: Achado) -> None:
        self._conferir_endereco(achado)
        # Why: keyed by the uuid alone, an answer carrying the uuid of the projector in the
        # next room would take over its entry, and the operator would then register the
        # address of the attacker under the real identity. Por que: chaveada só pelo uuid,
        # uma resposta com o uuid do projetor da sala ao lado tomaria a entrada dele, e o
        # operador cadastraria o endereço do atacante sob a identidade real.
        marca = "uuid" if achado.identidade else "st"
        chave = (marca, achado.ip, achado.identidade or achado.tipo)
        anterior = self.por_chave.get(chave)
        self.por_chave[chave] = achado if anterior is None else _mesclar(anterior, achado)

    def resultado(self) -> tuple[Achado, ...]:
        return tuple(sorted(self.por_chave.values(), key=lambda a: (a.ip, a.tipo, a.identidade)))

    def _conferir_endereco(self, achado: Achado) -> None:
        if not achado.identidade:
            return
        enderecos = self.ips_por_identidade.setdefault(achado.identidade, set())
        if achado.ip in enderecos:
            return
        enderecos.add(achado.ip)
        if len(enderecos) > 1:
            log.warning(
                "identity %r answered from more than one address (%s); every one is kept",
                achado.identidade,
                ", ".join(sorted(enderecos)),
            )
