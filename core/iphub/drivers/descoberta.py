# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Discovery generated from the manifests: an SSDP sweep that believes only the sender.

the plan is built from the manifests, never written by hand, and two manifests
that claim the same signature are a test error, not a decision taken in runtime.
in spirit: an answer is data, so the address of a device is the address the datagram came
from and never what the answer points at.

mDNS: procurar_mdns asks for the services the manifests declare, one shot over UDP the way
RFC 6762 defines a query that leaves an ephemeral port, with no background browser and no
dependency. The rule of the address holds there too: an A record that names a host other
than the sender is read and refused, never followed.
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

# One device answering in a loop on a customer LAN must not grow the answer nor the
# memory of this daemon; one ceiling bounds what a sweep finds, the other the work it does.
ACHADOS_MAXIMOS = 200
DATAGRAMAS_MAXIMOS = 5000

# MDNS, RFC 6762. A query that leaves an ephemeral port is a one shot query, so the answer
# comes back by unicast and this daemon needs neither the multicast group nor a browser.
DESTINO_MDNS = ("224.0.0.251", 5353)
SUFIXO_MDNS = ".local"

CABECALHO_DNS = 12
RESPOSTA_DNS = 0x8000
PONTEIRO_DNS = 0xC0
ROTULO_DO_PONTEIRO = 0x3F
CLASSE_IN = 1
# RFC 6762 sets the cache flush bit on the class of a unique record, so a real SRV and a
# real A arrive with class 0x8001 and only the low bits name the class.
MASCARA_CLASSE = 0x7FFF
TIPO_A = 1
TIPO_PTR = 12
TIPO_SRV = 33
TAMANHO_A = 4
TAMANHO_SRV_MINIMO = 7
CABECALHO_REGISTRO = 10
PORTA_MAXIMA = 65535

# A name that walks in circles and a message that claims ten thousand records are the
# two ways a datagram makes a reader work forever; each one meets a number here.
ROTULO_MAXIMO = 63
NOME_MAXIMO = 255
SALTOS_MAXIMOS = 16
REGISTROS_MAXIMOS = 64
INSTANCIAS_MAXIMAS = 32

# The uuid stops at the two colons that separate it from the service inside a USN, so
# the colon is out of the class on purpose.
_UUID = re.compile(r"uuid:([0-9A-Za-z][0-9A-Za-z._-]{0,127})")


@dataclass(frozen=True)
class Achado:
    """One device seen on the segment; tipo and identidade are empty when unknown."""

    tipo: str
    identidade: str
    ip: str
    porta: int | None
    descricao: str
    nome: str = ""


class PlanoAmbiguo(ValueError):
    """Two types claim the same signature, which the suite catches before any hub boots."""


@dataclass(frozen=True)
class Plano:
    """What to search and how to read the answer, generated from the manifests.

    mdns lists the services the manifests declare, in the form the manifest wrote them;
    procurar_mdns is what turns each one into a question on the wire.
    """

    sts: tuple[str, ...] = ()
    por_st: dict[str, str] = field(default_factory=dict)
    fabricantes: tuple[tuple[str, str], ...] = ()
    mdns: dict[str, str] = field(default_factory=dict)


def montar(manifestos: Iterable[Manifesto]) -> Plano:
    """The plan of every manifest handed in, refusing a signature two types claim."""
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
    """One M-SEARCH per target, then every answer that arrives before timeout_s, folded."""
    alvos = _alvos(plano)
    if not alvos:
        return ()
    coleta = _Coleta(plano)
    perguntas = tuple(_msearch(alvo, destino, timeout_s) for alvo in alvos)
    await _perguntar(perguntas, coleta, destino=destino, timeout_s=timeout_s, bind=bind)
    return coleta.resultado()


async def procurar_mdns(
    plano: Plano,
    *,
    destino: tuple[str, int] = DESTINO_MDNS,
    timeout_s: float = TIMEOUT_PADRAO,
    bind: tuple[str, int] = BIND_PADRAO,
) -> tuple[Achado, ...]:
    """One PTR query per declared service, then every answer before timeout_s, folded by
    instance name.
    """
    servicos = _servicos_mdns(plano)
    if not servicos:
        return ()
    coleta = _ColetaMdns(servicos)
    perguntas = tuple(
        pergunta for pergunta in map(_pergunta_mdns, servicos) if pergunta is not None
    )
    await _perguntar(perguntas, coleta, destino=destino, timeout_s=timeout_s, bind=bind)
    return coleta.resultado()


def _reivindicar(
    mapa: dict[str, str], chave: str, tipo: str, campo: str, conflitos: list[str]
) -> None:
    # An empty substring would match every answer on the segment, so it claims nothing.
    if not chave:
        return
    dono = mapa.setdefault(chave, tipo)
    if dono != tipo:
        conflitos.append(f"{campo} {chave!r} is claimed by {dono!r} and by {tipo!r}")


def _alvos(plano: Plano) -> tuple[str, ...]:
    """Every declared ST, plus the search for everything when a manufacturer is declared."""
    # A manufacturer substring is not something to ask for, it only reads an answer that
    # already arrived, so a plan that carries one has to ask the segment for everything.
    alvos = list(plano.sts)
    if plano.fabricantes and BUSCA_TOTAL not in alvos:
        alvos.append(BUSCA_TOTAL)
    return tuple(alvos)


def _mx(timeout_s: float) -> int:
    return max(MX_MINIMO, min(MX_MAXIMO, int(timeout_s)))


def _cabecalho_seguro(valor: str) -> str:
    # A manifest is data on disk, and a carriage return inside an ST would write a
    # header of its own into the datagram this daemon puts on the wire.
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


async def _perguntar(
    perguntas: tuple[bytes, ...],
    coleta: "_Coleta | _ColetaMdns",
    *,
    destino: tuple[str, int],
    timeout_s: float,
    bind: tuple[str, int],
) -> None:
    """Puts every question of one search on one socket and hands the answers to the coleta."""
    laco = asyncio.get_running_loop()
    soquete = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        soquete.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # A device flooding the group must not grow the kernel queue while the sweep
        # runs; what does not fit in the cap is dropped, and a sweep is repeatable.
        with suppress(OSError):
            soquete.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUFFER_RECEBIMENTO)
        soquete.setblocking(False)
        soquete.bind(bind)
        for pergunta in perguntas:
            await laco.sock_sendto(soquete, pergunta, destino)
        await _receber(laco, soquete, coleta, timeout_s)
    finally:
        soquete.close()


async def _receber(
    laco: asyncio.AbstractEventLoop,
    soquete: socket.socket,
    coleta: "_Coleta | _ColetaMdns",
    timeout_s: float,
) -> None:
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
            # An unreachable host on the segment surfaces on the next receive, and it is
            # not a reason to abandon a sweep that other devices are still answering.
            erros += 1
            continue
        erros = 0
        datagramas += 1
        coleta.absorver(dados, remetente[0])
        if coleta.quantos() >= ACHADOS_MAXIMOS:
            teto = f"ACHADOS_MAXIMOS ({ACHADOS_MAXIMOS})"
        elif datagramas >= DATAGRAMAS_MAXIMOS:
            teto = f"DATAGRAMAS_MAXIMOS ({DATAGRAMAS_MAXIMOS})"
        else:
            continue
        # A sweep cut short leaves a device off the list, and the reason has to be in
        log.warning("discovery sweep cut short by %s; a later answer is missing from it", teto)
        break


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
    """The headers of an SSDP answer, or None for a datagram that is not one."""
    texto = dados[:DATAGRAMA_MAXIMO].decode("utf-8", errors="ignore")
    linhas = texto.replace("\r\n", "\n").split("\n")
    partes = linhas[0].split()
    if len(partes) < 2 or not partes[0].upper().startswith("HTTP/1.") or partes[1] != "200":
        return None
    cabecalhos: dict[str, str] = {}
    for linha in linhas[1:]:
        # The empty line ends the headers, as HTTP defines, so a line of the body shaped
        # like a header cannot beat the header the device really sent.
        if not linha.strip():
            break
        chave, separador, valor = linha.partition(":")
        if separador:
            # The first occurrence wins, so a header repeated later in the same datagram
            # cannot overwrite the one already read.
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
    """The port of LOCATION, and only when LOCATION names the sender itself."""
    # The port is a hint for the registration form, and an answer that names another
    # host is pointing somewhere else, which is exactly what must not be followed.
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
    """Keeps the answer that names a tipo and completes it with what the other one carried."""
    base, extra = (antigo, novo) if antigo.tipo else (novo, antigo)
    return replace(
        base,
        identidade=base.identidade or extra.identidade,
        porta=base.porta if base.porta is not None else extra.porta,
        descricao=base.descricao or extra.descricao,
    )


@dataclass
class _Coleta:
    """The answer of a sweep while it is built: folded on arrival, one entry per device."""

    plano: Plano = field(default_factory=Plano)
    por_chave: dict[tuple[str, str, str], Achado] = field(default_factory=dict)
    ips_por_identidade: dict[str, set[str]] = field(default_factory=dict)

    def absorver(self, dados: bytes, ip: str) -> None:
        achado = _ler(dados, ip, self.plano)
        if achado is not None:
            self.guardar(achado)

    def quantos(self) -> int:
        return len(self.por_chave)

    def guardar(self, achado: Achado) -> None:
        self._conferir_endereco(achado)
        # Keyed by the uuid alone, an answer carrying the uuid of the projector in the
        # next room would take over its entry, and the operator would then register the
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


def _servicos_mdns(plano: Plano) -> dict[str, str]:
    """The services to ask for, in the form the wire uses, each pointing at the tipo that
    claims it.
    """
    servicos: dict[str, str] = {}
    for declarado, tipo in plano.mdns.items():
        nome = _nome_de_servico(declarado)
        # A manifest is data on disk, and a label longer than the wire admits would go
        # out as a malformed question that no device on the segment can answer.
        if nome is None or _nome_em_bytes(nome) is None:
            log.warning("mdns service %r of %r cannot be asked for and is skipped", declarado, tipo)
            continue
        dono = servicos.setdefault(nome, tipo)
        if dono != tipo:
            log.warning(
                "mdns service %r is claimed by %r and by %r; %r keeps it", nome, dono, tipo, dono
            )
    return servicos


def _nome_de_servico(declarado: str) -> str | None:
    nome = declarado.strip().strip(".").lower()
    if not nome:
        return None
    return nome if nome.endswith(SUFIXO_MDNS) else nome + SUFIXO_MDNS


def _nome_em_bytes(nome: str) -> bytes | None:
    """A name in the length prefixed form of DNS, or None when it does not fit the wire."""
    saida = bytearray()
    for parte in nome.split("."):
        bruto = parte.encode("utf-8", errors="ignore")
        if not bruto or len(bruto) > ROTULO_MAXIMO:
            return None
        saida += bytes([len(bruto)]) + bruto
    saida += b"\x00"
    return bytes(saida) if len(saida) <= NOME_MAXIMO else None


def _pergunta_mdns(nome: str) -> bytes | None:
    """One PTR question for one service, which is the whole query this daemon sends."""
    codificado = _nome_em_bytes(nome)
    if codificado is None:
        return None
    # RFC 6762 fixes the identifier of a query at zero, so it is not a token to check in
    # the answer, and the ceilings below are what bound a hostile one.
    cabecalho = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    return cabecalho + codificado + TIPO_PTR.to_bytes(2, "big") + CLASSE_IN.to_bytes(2, "big")


def _nome(dados: bytes, posicao: int) -> tuple[tuple[bytes, ...], int] | None:
    """The labels of a name and the position right after it, or None for a name that lies."""
    rotulos: list[bytes] = []
    tamanho = 0
    fim = -1
    saltos = 0
    limite = posicao
    while True:
        if posicao >= len(dados):
            return None
        marca = dados[posicao]
        if (marca & PONTEIRO_DNS) == PONTEIRO_DNS:
            if posicao + 1 >= len(dados):
                return None
            alvo = ((marca & ROTULO_DO_PONTEIRO) << 8) | dados[posicao + 1]
            if fim < 0:
                fim = posicao + 2
            saltos += 1
            # A pointer that does not go strictly backwards is how a datagram makes a
            # reader walk in circles, and a daemon walking in circles is a daemon that is
            if alvo < CABECALHO_DNS or alvo >= limite or saltos > SALTOS_MAXIMOS:
                return None
            limite = alvo
            posicao = alvo
            continue
        if marca & PONTEIRO_DNS:
            return None
        if marca == 0:
            return tuple(rotulos), fim if fim >= 0 else posicao + 1
        inicio = posicao + 1
        posicao = inicio + marca
        tamanho += marca + 1
        if posicao > len(dados) or tamanho > NOME_MAXIMO:
            return None
        rotulos.append(dados[inicio:posicao])


def _registros(dados: bytes) -> tuple[tuple[tuple[bytes, ...], int, int, int], ...] | None:
    """Every record of an answer as (name, type, where its data starts, how long it is).

    None is a datagram that is not an mDNS answer or that does not parse whole; what a
    message claims beyond REGISTROS_MAXIMOS is left on the wire.
    """
    if len(dados) < CABECALHO_DNS or not int.from_bytes(dados[2:4], "big") & RESPOSTA_DNS:
        return None
    contagens = [int.from_bytes(dados[i : i + 2], "big") for i in (4, 6, 8, 10)]
    posicao = CABECALHO_DNS
    for _ in range(contagens[0]):
        lido = _nome(dados, posicao)
        if lido is None:
            return None
        posicao = lido[1] + 4
    registros: list[tuple[tuple[bytes, ...], int, int, int]] = []
    for _ in range(min(sum(contagens[1:]), REGISTROS_MAXIMOS)):
        lido = _nome(dados, posicao)
        if lido is None:
            return None
        nome, posicao = lido
        if posicao + CABECALHO_REGISTRO > len(dados):
            return None
        tipo = int.from_bytes(dados[posicao : posicao + 2], "big")
        classe = int.from_bytes(dados[posicao + 2 : posicao + 4], "big") & MASCARA_CLASSE
        tamanho = int.from_bytes(dados[posicao + 8 : posicao + 10], "big")
        posicao += CABECALHO_REGISTRO
        if posicao + tamanho > len(dados):
            return None
        if classe == CLASSE_IN:
            registros.append((nome, tipo, posicao, tamanho))
        posicao += tamanho
    return tuple(registros)


@dataclass
class _InstanciaMdns:
    """What one answer said about one instance, before it becomes an Achado."""

    servico: str
    rotulo: str
    porta: int | None = None
    alvo: str | None = None


def _ler_mdns(dados: bytes, ip: str, servicos: dict[str, str]) -> tuple[tuple[str, Achado], ...]:
    """What one answer says about the instances of the declared services, by instance name."""
    registros = _registros(dados)
    if not registros:
        return ()
    enderecos: dict[str, str] = {}
    instancias: dict[str, _InstanciaMdns] = {}
    for nome, tipo, inicio, tamanho in registros:
        if tipo == TIPO_A and tamanho == TAMANHO_A:
            endereco = str(ipaddress.IPv4Address(dados[inicio : inicio + TAMANHO_A]))
            enderecos.setdefault(_texto_do_nome(nome), endereco)
        elif tipo == TIPO_PTR:
            apontado = _nome(dados, inicio)
            if apontado is not None:
                _anotar(instancias, apontado[0], servicos)
        elif tipo == TIPO_SRV and tamanho >= TAMANHO_SRV_MINIMO:
            alvo = _nome(dados, inicio + 6)
            _anotar(
                instancias,
                nome,
                servicos,
                porta=int.from_bytes(dados[inicio + 4 : inicio + 6], "big"),
                alvo=_texto_do_nome(alvo[0]) if alvo is not None else None,
            )
    return tuple(
        _achado_da_instancia(nome, instancia, servicos, enderecos, ip)
        for nome, instancia in instancias.items()
    )


def _anotar(
    instancias: dict[str, _InstanciaMdns],
    rotulos: tuple[bytes, ...],
    servicos: dict[str, str],
    *,
    porta: int | None = None,
    alvo: str | None = None,
) -> None:
    """Files one record under the instance it names, and only for a service we asked for."""
    if not rotulos:
        return
    nome = _texto_do_nome(rotulos)
    servico = _servico_da_instancia(nome, servicos)
    if servico is None:
        return
    instancia = instancias.get(nome)
    if instancia is None:
        if len(instancias) >= INSTANCIAS_MAXIMAS:
            return
        instancia = _InstanciaMdns(
            servico=servico, rotulo=_texto_limpo(_texto_do_rotulo(rotulos[0]))
        )
        instancias[nome] = instancia
    if porta is not None:
        instancia.porta = porta
    if alvo:
        instancia.alvo = alvo


def _servico_da_instancia(nome: str, servicos: dict[str, str]) -> str | None:
    # The instance name carries its own service, so a PTR and an SRV that arrive in
    # different datagrams still name the same tipo without one having to trust the other.
    for servico in servicos:
        if nome.endswith("." + servico):
            return servico
    return None


def _achado_da_instancia(
    nome: str,
    instancia: _InstanciaMdns,
    servicos: dict[str, str],
    enderecos: dict[str, str],
    ip: str,
) -> tuple[str, Achado]:
    """The finding of one instance: the ip of the A record, and the port of the SRV."""
    apontado = enderecos.get(instancia.alvo) if instancia.alvo else None
    porta = instancia.porta
    if apontado is not None and apontado != ip:
        # . Following the address an answer points at is how this hub becomes a
        # proxy for the LAN of the customer, so an answer that names another host keeps only
        log.warning(
            "mdns answer from %s points instance %r at %s; the address is not followed",
            ip,
            nome,
            apontado,
        )
        apontado, porta = None, None
    return nome, Achado(
        tipo=servicos[instancia.servico],
        # Makes an identity a uuid, a mac or a serial, and an instance name is
        # none of the three; the driver reads the real identity when the equipment is
        identidade="",
        ip=apontado or ip,
        porta=porta if porta is not None and 0 < porta <= PORTA_MAXIMA else None,
        descricao=instancia.rotulo,
    )


def _texto_do_rotulo(rotulo: bytes) -> str:
    return rotulo.decode("utf-8", errors="replace")


def _texto_do_nome(rotulos: tuple[bytes, ...]) -> str:
    # DNS compares names without case, so the comparison here does the same or an
    # instance of "_LinkPlay._tcp.local" would answer for nobody.
    return ".".join(_texto_do_rotulo(rotulo) for rotulo in rotulos).lower()


@dataclass
class _ColetaMdns:
    """The answer of an mDNS query while it is built: one entry per instance of one sender."""

    servicos: dict[str, str]
    por_chave: dict[tuple[str, str], Achado] = field(default_factory=dict)

    def absorver(self, dados: bytes, ip: str) -> None:
        for nome, achado in _ler_mdns(dados, ip, self.servicos):
            # A device answers the PTR in one datagram and the SRV with the A in the
            # next, so the fold is by instance name and never by datagram.
            anterior = self.por_chave.get((ip, nome))
            self.por_chave[(ip, nome)] = achado if anterior is None else _mesclar(anterior, achado)

    def quantos(self) -> int:
        return len(self.por_chave)

    def resultado(self) -> tuple[Achado, ...]:
        return tuple(sorted(self.por_chave.values(), key=lambda a: (a.ip, a.tipo, a.descricao)))


# The two pieces of DNS the responder of the hub's own name shares with this module.
ler_nome = _nome
nome_em_bytes = _nome_em_bytes
