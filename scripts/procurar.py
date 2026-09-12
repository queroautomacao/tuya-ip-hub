#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Inventory of the local segment: every device with ip, mac and name, and on top of
that which ones this hub drives and by which driver.

Docker Desktop on macOS does not reach the LAN by multicast, so the
integrator runs this on the Mac and never inside the container. Every probe here is
read only: it opens tcp and closes it, or sends a question datagram, and nothing ever
changes the state of a device.

The driver column comes from iphub itself: the plan and the two multicast sweeps are
iphub.drivers.descoberta, and the unicast probe of each driver is the identificar of
 that the driver already carries. There is no second implementation here to
diverge from the hub, so what this table marks is what the hub would find.
"""

import argparse
import asyncio
import contextlib
import importlib.util
import ipaddress
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

try:
    import resource
except ImportError:  # pragma: no cover
    resource = None

# The shebang finds whatever python3 the PATH has, and on macOS that is the 3.9 of
# the system, where the first annotation of this file and of iphub is a TypeError. The
# check comes before the import of the package so what the operator reads is the
# interpreter to use and not a traceback about the union operator.
if sys.version_info < (3, 12):
    print(
        "procurar: este script precisa do Python 3.12 e achou o "
        f"{sys.version_info.major}.{sys.version_info.minor}; rode com "
        "core/.venv/bin/python scripts/procurar.py",
        file=sys.stderr,
    )
    raise SystemExit(2)

_NUCLEO = Path(__file__).resolve().parent.parent / "core"
_VENV = _NUCLEO / ".venv" / "bin" / "python"
_JA_TROCOU = "IPHUB_PROCURAR_INTERPRETADOR"

# The drivers are imported one by one to be asked who they drive, and the ones that
# speak HTTP import aiohttp at module level, which lives in core/.venv and nowhere else.
# An interpreter without it gets through the imports below, because those need no
# dependency, and only breaks pages later inside the catalogue, as a traceback about a
# module the operator never named. So the interpreter is swapped here, before anything is
# loaded: whoever runs `python3 procurar.py` gets the run they asked for instead of a
# lesson about virtual environments. The variable in the environment is what makes a
# broken venv fail instead of swapping forever.
if importlib.util.find_spec("aiohttp") is None:
    if _VENV.is_file() and not os.environ.get(_JA_TROCOU):
        os.environ[_JA_TROCOU] = "1"
        os.execv(str(_VENV), [str(_VENV), str(Path(__file__).resolve()), *sys.argv[1:]])
    print(
        "procurar: os drivers deste hub precisam da dependencia que mora em "
        f"{_VENV}, e este interpretador nao a tem.",
        file=sys.stderr,
    )
    print(
        "procurar: rode com core/.venv/bin/python scripts/procurar.py, ou crie o "
        "ambiente com: cd core && python3.12 -m venv .venv && .venv/bin/pip install -e .",
        file=sys.stderr,
    )
    raise SystemExit(2)

if _NUCLEO.is_dir() and str(_NUCLEO) not in sys.path:
    sys.path.insert(0, str(_NUCLEO))

try:
    from iphub.drivers import descoberta, nativos
    from iphub.drivers.catalogo import carregar_pacote
    from iphub.drivers.manifesto import TipoCampo
    from iphub.drivers.nativos import pjlink
except ImportError as _erro:  # pragma: no cover
    print(f"procurar: {_erro}", file=sys.stderr)
    print(
        "procurar: rode com core/.venv/bin/python, que é onde o iphub e a "
        "dependência dele estão instalados",
        file=sys.stderr,
    )
    raise SystemExit(2) from _erro

log = logging.getLogger("procurar")

# The ARP table only knows a host somebody spoke to, and one empty datagram per
# address makes the kernel resolve the MAC on its own. It is three orders of magnitude
# cheaper than ping and needs no privilege; the port is high and unassigned because
# only the ARP that precedes the datagram matters.
PORTA_DESPERTAR = 33435
ESPERA_ARP_S = 0.4

# A /22 is 1022 addresses and the kernel holds 1024 packets waiting for ARP in
# total, so a wider prefix loses answers and takes minutes. The bridge of Docker on
# macOS is a /16, and meeting one is the signal, not a range to sweep.
PREFIXO_MINIMO = 22
PREFIXO_MAXIMO = 30

# The ceiling is descriptors, not speed. With a soft limit of 256 (what launchd
# hands a process) a sweep of 254 probes loses four to EMFILE, and an EMFILE looks
# exactly like an absent host, so the reserve is what separates the sweep from a silent
# false negative.
TETO_SONDAS = 256
RESERVA_DESCRITORES = 32
TETO_SEM_RESOURCE = 64

# With no ARP table there is no list of who is alive, so the probes go over the
# whole range and the deadline has to shrink or a /24 against a dozen drivers passes
# the thirty seconds this script promises.
PRAZO_FAIXA_CEGA_S = 1.0
PRAZO_MINIMO_S = 0.5

FECHAMENTO_S = 1.0
SAUDACAO_MAXIMA = 512
TRABALHADORES_DNS = 8
NOME_MAXIMO = 60

CAMPO_PORTA = "porta"
MDNS_PORTA = 5353
SUFIXO_REVERSO = ".in-addr.arpa"

# The projector is the one driver of the catalogue that announces nothing on the
# segment and cannot say who it is, which its own manifest states on purpose, so the
# only way an inventory sees one is to open its port and READ the greeting, writing
# nothing. The word comes from the driver module and the port from its manifest, so
# this probe cannot drift away from the driver.
SAUDACOES = {pjlink.PJLink.MANIFESTO.tipo: pjlink.SAUDACAO}

# No base of OUI prefixes is installed on macOS or on any of the Linux images this
# project builds on, and downloading one would be third party data with a licence of
# its own inside the image. So the column is filled when a base happens to
# be there, and stays honestly empty when it is not.
BASES_OUI = (
    "/usr/share/wireshark/manuf",
    "/usr/local/share/wireshark/manuf",
    "/opt/homebrew/share/wireshark/manuf",
    "/Applications/Wireshark.app/Contents/Resources/share/wireshark/manuf",
    "/usr/share/nmap/nmap-mac-prefixes",
    "/usr/local/share/nmap/nmap-mac-prefixes",
    "/opt/homebrew/share/nmap/nmap-mac-prefixes",
    "/usr/share/ieee-data/oui.txt",
    "/usr/share/misc/oui.txt",
    "/usr/share/arp-scan/ieee-oui.txt",
)
FABRICANTE_MAXIMO = 28

# BSD and Linux number the same two questions differently, and the address of the
# answer sits at the same offset in both.
IOCTL_ENDERECO = {"darwin": 0xC0206921, "linux": 0x8915}
IOCTL_MASCARA = {"darwin": 0xC0206925, "linux": 0x891B}
ENDERECO_NO_SOCKADDR = slice(20, 24)

MAC_NULO = "00:00:00:00:00:00"
MAC_DIFUSAO = "ff:ff:ff:ff:ff:ff"
PREFIXO_MULTICAST = "01:00:5e"
INCOMPLETO = ("(incomplete)", "<incomplete>")

# The order of quality of a name, from the one that reads best to the one that reads
FONTES_DE_NOME = ("mdns", "ptr", "dns", "ssdp")

COLUNAS = (
    ("IP", "ip", 15),
    ("MAC", "mac", 17),
    ("NOME", "nome", 30),
    ("FABRICANTE", "fabricante", 20),
    ("DRIVER", "driver", 24),
    ("COMO", "como", 18),
)


@dataclass
class Cega:
    """One probe that could not run, or that ran and saw nothing while other evidence
    says the segment is not empty, with what the operator does about it.
    """

    sonda: str
    motivo: str
    acao: str


@dataclass
class Aparelho:
    """One device of the segment, however many paths found it."""

    ip: str
    mac: str = ""
    fabricante: str = ""
    fabricante_fonte: str = ""
    nomes: dict[str, str] = field(default_factory=dict)
    drivers: dict[str, str] = field(default_factory=dict)
    como: set[str] = field(default_factory=set)

    def nome(self) -> tuple[str, str]:
        for fonte in FONTES_DE_NOME:
            texto = self.nomes.get(fonte, "").strip()
            if texto:
                return texto[:NOME_MAXIMO], fonte
        return "", ""


def _teto_de_sondas() -> int:
    """How many probes may be in flight at once, sized by the descriptor limit."""
    if resource is None:
        return TETO_SEM_RESOURCE
    flexivel = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if flexivel < 0:
        return TETO_SONDAS
    return max(16, min(TETO_SONDAS, flexivel - RESERVA_DESCRITORES))


def _ip_de_saida() -> str:
    """The source address of the default route, with no byte on the wire."""
    # Connect on a datagram sends nothing, it only makes the kernel pick the
    # route, and getsockname then gives the source address it would use. The
    # destination is TEST-NET-1, which never exists.
    soquete = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        soquete.connect(("192.0.2.1", PORTA_DESPERTAR))
        return str(soquete.getsockname()[0])
    finally:
        soquete.close()


def _interface_do(ip: str) -> tuple[str, str] | None:
    """The interface that carries that address and its netmask, or None."""
    plataforma = "darwin" if sys.platform == "darwin" else "linux"
    if fcntl is None or plataforma not in IOCTL_ENDERECO:
        return None
    soquete = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _indice, nome in socket.if_nameindex():
            # On BSD the netmask comes back in a sockaddr whose sa_len is short (5
            # for a /8, 7 for a /24) and the bytes past it are whatever the buffer
            # carried, so the buffer is born zeroed and the read is always the same
            pedido = struct.pack("16s16s", nome.encode()[:15], b"\x00" * 16)
            try:
                bruto = fcntl.ioctl(soquete.fileno(), IOCTL_ENDERECO[plataforma], pedido)
                if socket.inet_ntoa(bruto[ENDERECO_NO_SOCKADDR]) != ip:
                    continue
                bruto = fcntl.ioctl(soquete.fileno(), IOCTL_MASCARA[plataforma], pedido)
            except OSError:
                continue
            return nome, socket.inet_ntoa(bruto[ENDERECO_NO_SOCKADDR])
    finally:
        soquete.close()
    return None


def _faixa(pedida: str, avisos: list[str]) -> tuple[ipaddress.IPv4Network, str, str]:
    """The range to sweep, the interface it was read from and the address of this host."""
    ip_local = _ip_de_saida()
    if pedida:
        rede = ipaddress.ip_network(pedida, strict=False)
        if not isinstance(rede, ipaddress.IPv4Network):
            raise ValueError(f"a faixa {pedida!r} não é IPv4")
        interface = ""
    else:
        lido = _interface_do(ip_local)
        if lido is None:
            avisos.append(
                "a máscara da interface não foi lida neste sistema; a faixa assumida "
                f"é {ip_local}/24, e --faixa CIDR corrige isso"
            )
            rede, interface = ipaddress.ip_network(f"{ip_local}/24", strict=False), ""
        else:
            interface, mascara = lido
            rede = ipaddress.IPv4Interface(f"{ip_local}/{mascara}").network
    if not PREFIXO_MINIMO <= rede.prefixlen <= PREFIXO_MAXIMO:
        raise ValueError(
            f"a faixa {rede} tem prefixo /{rede.prefixlen} e este script varre de "
            f"/{PREFIXO_MINIMO} a /{PREFIXO_MAXIMO}; passe uma sub faixa em --faixa. "
            "Um /16 costuma ser a bridge do Docker, e a seção 14 diz que dali não se "
            "alcança a LAN"
        )
    return rede, interface, ip_local


def _despertar(enderecos: list[str]) -> int:
    """One empty datagram per address, so the kernel resolves each MAC by itself."""
    soquete = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    enviados = 0
    try:
        soquete.setblocking(False)
        for endereco in enderecos:
            # The broadcast address of the range refuses the datagram with EACCES
            # unless SO_BROADCAST is set, and hosts already leaves it out; every other
            # refusal is one address that will simply not be in the table.
            with contextlib.suppress(OSError):
                soquete.sendto(b"", (endereco, PORTA_DESPERTAR))
                enviados += 1
    finally:
        soquete.close()
    return enviados


def _mac_normal(bruto: str) -> str | None:
    """The MAC in the one form this script compares, or None for what is not one."""
    octetos = bruto.split(":")
    if len(octetos) != 6:
        return None
    try:
        # MacOS prints an octet without the leading zero (f2:b6:7c:1:7a:dd), so a
        # comparison of raw strings breaks in silence.
        return ":".join(f"{int(octeto, 16):02x}" for octeto in octetos)
    except ValueError:
        return None


def _mac_de_aparelho(mac: str, ip: str) -> bool:
    if mac in (MAC_NULO, MAC_DIFUSAO) or mac.startswith(PREFIXO_MULTICAST):
        return False
    return not ip.startswith(("224.", "239.", "255."))


def _arp_do_sistema() -> tuple[dict[str, str], str]:
    """Every address of the ARP table with its MAC, and the source it was read from."""
    caminho = Path("/proc/net/arp")
    if caminho.exists():
        with contextlib.suppress(OSError):
            return _ler_proc_arp(caminho.read_text(encoding="utf-8", errors="replace")), str(
                caminho
            )
    for programa in ("/usr/sbin/arp", "/sbin/arp", "arp"):
        try:
            # Without -n the command does a reverse DNS lookup per entry, which
            # measured 2,84 s against 0,01 s on the same table.
            saida = subprocess.run(
                [programa, "-an"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if saida.returncode == 0:
            return _ler_arp_bsd(saida.stdout), f"{programa} -an"
    return {}, ""


def _ler_arp_bsd(saida: str) -> dict[str, str]:
    tabela: dict[str, str] = {}
    for linha in saida.splitlines():
        partes = linha.split()
        # "? (192.0.2.1) at 44:89:6d:31:55:e0 on en0 ifscope [ethernet]", and an entry
        # the kernel never resolved carries "(incomplete)" where the MAC would be.
        if len(partes) < 4 or partes[2] != "at" or partes[3] in INCOMPLETO:
            continue
        mac = _mac_normal(partes[3])
        ip = partes[1].strip("()")
        if mac is not None and _mac_de_aparelho(mac, ip):
            tabela.setdefault(ip, mac)
    return tabela


def _ler_proc_arp(texto: str) -> dict[str, str]:
    tabela: dict[str, str] = {}
    for linha in texto.splitlines()[1:]:
        partes = linha.split()
        # Flags 0x2 is a complete entry and 0x0 is one the kernel gave up on, which
        if len(partes) < 4 or partes[2] == "0x0":
            continue
        mac = _mac_normal(partes[3])
        if mac is not None and _mac_de_aparelho(mac, partes[0]):
            tabela.setdefault(partes[0], mac)
    return tabela


def _base_oui() -> tuple[dict[str, str], str]:
    """A local table of prefix to manufacturer, when this machine happens to have one."""
    for caminho in BASES_OUI:
        arquivo = Path(caminho)
        if not arquivo.is_file():
            continue
        with contextlib.suppress(OSError):
            return _ler_base_oui(arquivo), caminho
    return {}, ""


def _ler_base_oui(arquivo: Path) -> dict[str, str]:
    tabela: dict[str, str] = {}
    with arquivo.open(encoding="utf-8", errors="replace") as aberto:
        for bruta in aberto:
            linha = bruta.split("#", 1)[0].strip()
            partes = linha.split(None, 1)
            if len(partes) != 2:
                continue
            prefixo, _barra, bits = partes[0].partition("/")
            # The manuf file also carries prefixes longer than 24 bits, and those
            if bits and bits != "24":
                continue
            prefixo = prefixo.replace(":", "").replace("-", "").lower()
            if len(prefixo) != 6 or any(c not in "0123456789abcdef" for c in prefixo):
                continue
            nome = partes[1].replace("(hex)", " ").strip().split("\t")[0].strip()
            if nome:
                tabela.setdefault(prefixo, nome[:FABRICANTE_MAXIMO])
    return tabela


def _fabricante(mac: str, base: dict[str, str]) -> str:
    if not mac or not base:
        return ""
    # The second bit of the first octet means the address was made up locally, and
    # a made up address answers to no manufacturer at all.
    if int(mac[:2], 16) & 0x02:
        return ""
    return base.get(mac.replace(":", "")[:6], "")


def _nomes_por_dns(enderecos: list[str], prazo_s: float) -> dict[str, str]:
    """The reverse DNS of each address, in threads this script never waits forever on.

    Gethostbyaddr is the resolver of libc, it ignores socket.setdefaulttimeout, it
    takes ten seconds where no server answers and it cannot be interrupted. So the
    threads are daemons: whatever did not answer inside the deadline is dropped and
    never holds the exit of the process.
    """
    pendentes = list(enderecos)
    achados: dict[str, str] = {}
    trava = threading.Lock()

    def trabalhar() -> None:
        while True:
            with trava:
                if not pendentes:
                    return
                endereco = pendentes.pop()
            try:
                nome = socket.gethostbyaddr(endereco)[0]
            except OSError:
                continue
            if nome:
                achados[endereco] = nome

    fios = [
        threading.Thread(target=trabalhar, daemon=True)
        for _ in range(min(TRABALHADORES_DNS, max(1, len(enderecos))))
    ]
    for fio in fios:
        fio.start()
    fim = time.monotonic() + prazo_s
    for fio in fios:
        fio.join(max(0.0, fim - time.monotonic()))
    return dict(achados)


class _ColetaReversa:
    """PTR answers, read with the mDNS reader of the hub and kept by sender.

    On the group anyone may answer for anybody, so an answer only counts when the
    question it answers is the reverse name of the sender itself. It is the same rule
    the hub applies to an A record that names another host, and it is what keeps a
    stranger from naming the projector of the next room.
    """

    def __init__(self) -> None:
        self.nomes: dict[str, str] = {}

    def absorver(self, dados: bytes, ip: str) -> None:
        registros = descoberta._registros(dados)
        if not registros:
            return
        esperado = _nome_reverso(ip).lower()
        for nome, tipo, inicio, _tamanho in registros:
            if tipo != descoberta.TIPO_PTR or descoberta._texto_do_nome(nome) != esperado:
                continue
            lido = descoberta._nome(dados, inicio)
            if lido is None:
                continue
            # The reader of the hub lowercases a name on purpose, because DNS
            # compares without case, but the operator reads the name the device chose
            # for itself, so the labels are decoded here with the case they came in.
            texto = ".".join(descoberta._texto_do_rotulo(r) for r in lido[0]).strip(".")
            if texto:
                self.nomes.setdefault(ip, texto)

    def quantos(self) -> int:
        return len(self.nomes)


def _nome_reverso(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + SUFIXO_REVERSO


def _perguntas_reversas(enderecos: list[str]) -> tuple[bytes, ...]:
    perguntas = (descoberta._pergunta_mdns(_nome_reverso(ip)) for ip in enderecos)
    return tuple(pergunta for pergunta in perguntas if pergunta is not None)


async def _ptr_unicast(
    enderecos: list[str],
    prazo_s: float,
    limite: asyncio.Semaphore,
    bind: tuple[str, int],
) -> dict[str, str]:
    """The.local name of each address, asked to the device itself over unicast mDNS.

    A query sent straight to port 5353 of one host is one datagram to one host,
    it isolates the answer to that device and it does not depend on the multicast
    route, so this column survives where says the multicast sweep finds
    nothing.
    """
    achados: dict[str, str] = {}

    async def perguntar(ip: str) -> None:
        perguntas = _perguntas_reversas([ip])
        if not perguntas:
            return
        coleta = _ColetaReversa()
        async with limite:
            with contextlib.suppress(OSError):
                await descoberta._perguntar(
                    perguntas,
                    coleta,
                    destino=(ip, MDNS_PORTA),
                    timeout_s=prazo_s,
                    bind=bind,
                )
        achados.update(coleta.nomes)

    await asyncio.gather(*(perguntar(ip) for ip in enderecos))
    return achados


async def _ptr_grupo(enderecos: list[str], prazo_s: float, bind: tuple[str, int]) -> dict[str, str]:
    """The same question asked once to the group, which answers hosts unicast misses.

    Measured on this segment the two forms bring slightly different sets, so the
    group is asked as well and the two are merged. It only makes sense over a short
    list of addresses already known to be alive, so the caller does not run it when
    there is no ARP table to build that list from.
    """
    perguntas = _perguntas_reversas(enderecos)
    if not perguntas:
        return {}
    coleta = _ColetaReversa()
    with contextlib.suppress(OSError):
        await descoberta._perguntar(
            perguntas,
            coleta,
            destino=descoberta.DESTINO_MDNS,
            timeout_s=prazo_s,
            bind=bind,
        )
    return dict(coleta.nomes)


async def _sondar_identificar(
    enderecos: list[str],
    classes: dict[str, type],
    prazo_s: float,
    limite: asyncio.Semaphore,
) -> dict[str, dict[str, str]]:
    """What each driver answers when asked, on each address: the identity.

    This is the driver own read only probe, the same one the sweep of the panel
    uses, so a driver that answers here is a driver the hub would register. A driver
    that does not carry one is left out instead of guessed at from an open port.
    """
    achados: dict[str, dict[str, str]] = {}

    async def uma(ip: str, tipo: str, classe: type) -> tuple[str, str, str | None]:
        async with limite:
            return ip, tipo, await asyncio.wait_for(classe.identificar(ip), prazo_s)

    # A diagnostic must never die because one driver raised against one stranger
    # of the segment, and the sweep of the panel folds the same way, so what a probe
    # raised is collected and logged instead of taking the other eleven down with it.
    lidos = await asyncio.gather(
        *(uma(ip, tipo, classe) for ip in enderecos for tipo, classe in classes.items()),
        return_exceptions=True,
    )
    for lido in lidos:
        if isinstance(lido, BaseException):
            log.debug("uma sonda de driver levantou: %s", lido)
            continue
        ip, tipo, identidade = lido
        if identidade:
            achados.setdefault(ip, {})[tipo] = identidade
    return achados


async def _sondar_saudacao(
    enderecos: list[str],
    portas: dict[str, tuple[int, str]],
    prazo_s: float,
    limite: asyncio.Semaphore,
) -> dict[str, set[str]]:
    """Opens the port a driver declares and READS the greeting, writing nothing."""
    achados: dict[str, set[str]] = {}

    async def uma(ip: str, tipo: str, porta: int, palavra: str) -> None:
        async with limite:
            try:
                leitor, escritor = await asyncio.wait_for(
                    asyncio.open_connection(ip, porta), prazo_s
                )
            except (OSError, ValueError):
                return
            try:
                bruto = await asyncio.wait_for(leitor.read(SAUDACAO_MAXIMA), prazo_s)
            except (OSError, ValueError):
                bruto = b""
            finally:
                escritor.close()
                with contextlib.suppress(OSError):
                    await asyncio.wait_for(escritor.wait_closed(), FECHAMENTO_S)
        saudacao = bruto.decode("ascii", errors="replace").strip().upper()
        if saudacao.startswith(palavra.upper()):
            achados.setdefault(ip, set()).add(tipo)

    await asyncio.gather(
        *(
            uma(ip, tipo, porta, palavra)
            for ip in enderecos
            for tipo, (porta, palavra) in portas.items()
        )
    )
    return achados


async def _varrer_hub(
    funcao: Callable[..., Awaitable[tuple[descoberta.Achado, ...]]],
    plano: descoberta.Plano,
    prazo_s: float,
    bind: tuple[str, int],
) -> tuple[tuple[descoberta.Achado, ...], OSError | None]:
    """One of the two multicast sweeps of the hub, with the refusal it may hit."""
    tentativas = [bind]
    if bind != descoberta.BIND_PADRAO:
        tentativas.append(descoberta.BIND_PADRAO)
    ultimo: OSError | None = None
    for tentativa in tentativas:
        try:
            return await funcao(plano, timeout_s=prazo_s, bind=tentativa), None
        except OSError as erro:
            ultimo = erro
    return (), ultimo


def _catalogo() -> tuple[dict[str, type], dict[str, type], dict[str, tuple[int, str]], list[str]]:
    """The whole native catalogue, split by what a sweep can do about each driver."""
    classes = carregar_pacote(nativos)
    com_identificar: dict[str, type] = {}
    com_saudacao: dict[str, tuple[int, str]] = {}
    sem_sonda: list[str] = []
    for tipo, classe in classes.items():
        manifesto = classe.MANIFESTO
        if manifesto.nuvem:
            sem_sonda.append(f"{tipo} (nuvem, não há nada na LAN para achar)")
            continue
        if "identificar" in vars(classe):
            com_identificar[tipo] = classe
            continue
        palavra = SAUDACOES.get(tipo)
        porta = _porta_declarada(manifesto)
        if palavra and porta:
            com_saudacao[tipo] = (porta, palavra)
            continue
        sem_sonda.append(f"{tipo} (sem identificar; só aparece por SSDP ou mDNS)")
    return classes, com_identificar, com_saudacao, sem_sonda


def _porta_declarada(manifesto) -> int:
    for campo in manifesto.config_campos:
        if campo.nome == CAMPO_PORTA and campo.tipo is TipoCampo.INTEIRO:
            with contextlib.suppress(ValueError):
                return int(campo.padrao)
    return 0


async def _nada() -> dict[str, str]:
    return {}


def _guardar(por_ip: dict[str, Aparelho], ip: str) -> Aparelho:
    aparelho = por_ip.get(ip)
    if aparelho is None:
        aparelho = Aparelho(ip=ip)
        por_ip[ip] = aparelho
    return aparelho


async def _correr(args, rede, ip_local: str, cegas: list[Cega]) -> dict:
    """The whole sweep: the two multicast paths of the hub next to the unicast ones."""
    classes, com_identificar, com_saudacao, sem_sonda = _catalogo()
    plano = descoberta.montar(classe.MANIFESTO for classe in classes.values())
    limite = asyncio.Semaphore(_teto_de_sondas())
    executadas = ["arp", "ssdp", "mdns", "ptr reverso", "dns reverso", "porta"]
    por_ip: dict[str, Aparelho] = {}
    estado: dict[str, object] = {}

    # The source address is pinned so the question always leaves by the address of
    # the LAN, and a bind that the system refuses falls back to the default instead of
    # taking the sweep down with it.
    bind = (ip_local, 0)

    async def caminho_unicast() -> None:
        _despertar([str(e) for e in rede.hosts() if str(e) != ip_local])
        await asyncio.sleep(ESPERA_ARP_S)
        tabela, fonte = _arp_do_sistema()
        estado["fonte_arp"] = fonte
        vivos = sorted(
            (ip for ip in tabela if ipaddress.ip_address(ip) in rede and ip != ip_local),
            key=lambda ip: int(ipaddress.ip_address(ip)),
        )
        for ip in vivos:
            aparelho = _guardar(por_ip, ip)
            aparelho.mac = tabela[ip]
            aparelho.como.add("arp")
        if fonte:
            alvos, prazo = vivos, args.timeout
        else:
            # With no ARP table there is no list of who is alive, so the probes go
            # over the whole range and the deadline shrinks to keep the promise of
            alvos = [str(e) for e in rede.hosts() if str(e) != ip_local]
            prazo = min(args.timeout, PRAZO_FAIXA_CEGA_S)
        prazo = max(prazo, PRAZO_MINIMO_S)
        estado["alvos"] = len(alvos)
        grupo = _ptr_grupo(vivos, prazo, bind) if fonte else _nada()
        ptr, ptr_grupo, identidades, saudacoes = await asyncio.gather(
            _ptr_unicast(alvos, prazo, limite, bind),
            grupo,
            _sondar_identificar(alvos, com_identificar, prazo, limite),
            _sondar_saudacao(alvos, com_saudacao, prazo, limite),
        )
        for achado_ptr in (ptr, ptr_grupo):
            for ip, nome in achado_ptr.items():
                # A device that named itself over mDNS answered a question, which
                # is evidence it is there, so the path goes in the COMO column; the
                # reverse DNS below never does, because that name comes from a server
                # that may be repeating a record of a device long gone.
                aparelho = _guardar(por_ip, ip)
                aparelho.nomes.setdefault("ptr", nome)
                aparelho.como.add("ptr")
        for ip, achado in identidades.items():
            aparelho = _guardar(por_ip, ip)
            aparelho.drivers.update(achado)
            aparelho.como.add("porta")
        for ip, tipos in saudacoes.items():
            aparelho = _guardar(por_ip, ip)
            for tipo in tipos:
                aparelho.drivers.setdefault(tipo, "")
            aparelho.como.add("porta")
        estado["vivos"] = len(vivos)

    ssdp, mdns, _ = await asyncio.gather(
        _varrer_hub(descoberta.procurar, plano, args.timeout, bind),
        _varrer_hub(descoberta.procurar_mdns, plano, args.timeout, bind),
        caminho_unicast(),
    )
    fora = 0
    for achados, marca in ((ssdp[0], "ssdp"), (mdns[0], "mdns")):
        for achado in achados:
            if achado.ip == ip_local:
                continue
            # The two multicast sweeps answer for the whole segment, and --faixa
            # is the scope of this inventory, so what falls outside it is counted and
            # said in the footer instead of landing in a table it does not belong to.
            if ipaddress.ip_address(achado.ip) not in rede:
                fora += 1
                continue
            aparelho = _guardar(por_ip, achado.ip)
            aparelho.como.add(marca)
            if achado.tipo:
                aparelho.drivers.setdefault(achado.tipo, achado.identidade)
            if achado.descricao:
                aparelho.nomes.setdefault(marca, achado.descricao)

    # A TCP probe also fills the ARP table, so a second read catches the device
    # that was still waking up on the first one, and it costs one command.
    tabela, _fonte = _arp_do_sistema()
    for ip, mac in tabela.items():
        if ip == ip_local or ipaddress.ip_address(ip) not in rede:
            continue
        aparelho = _guardar(por_ip, ip)
        aparelho.mac = aparelho.mac or mac
        aparelho.como.add("arp")

    dns = _nomes_por_dns(sorted(por_ip), min(args.timeout, 2.0))
    for ip, nome in dns.items():
        _guardar(por_ip, ip).nomes.setdefault("dns", nome)

    base, caminho_oui = _base_oui()
    for aparelho in por_ip.values():
        nome_do_oui = _fabricante(aparelho.mac, base)
        if nome_do_oui:
            aparelho.fabricante, aparelho.fabricante_fonte = nome_do_oui, "oui"
            continue
        # With no OUI base the manifest of a driver that answered is a better
        # source than a prefix would be, because it names who made the box and not who
        marcas = {classes[t].MANIFESTO.marca for t in aparelho.drivers if t in classes}
        marcas.discard("")
        if marcas:
            aparelho.fabricante = ", ".join(sorted(marcas))[:FABRICANTE_MAXIMO]
            aparelho.fabricante_fonte = "driver"

    _diagnosticar(cegas, estado, ssdp[1], mdns[1], caminho_oui)
    return {
        "aparelhos": por_ip,
        "classes": classes,
        "executadas": executadas,
        "sem_sonda": sem_sonda,
        "sondas_de_driver": len(com_identificar) + len(com_saudacao),
        "fonte_oui": caminho_oui,
        "ssdp": len(ssdp[0]),
        "mdns": len(mdns[0]),
        "alvos_ssdp": len(descoberta._alvos(plano)),
        "alvos_mdns": len(descoberta._servicos_mdns(plano)),
        "fora_da_faixa": fora,
        "multicast_recusado": bool(ssdp[1] or mdns[1]),
        "vivos": int(estado.get("vivos", 0)),
        "alvos": int(estado.get("alvos", 0)),
    }


def _diagnosticar(
    cegas: list[Cega],
    estado: dict,
    erro_ssdp: OSError | None,
    erro_mdns: OSError | None,
    caminho_oui: str,
) -> None:
    """What each probe could not do, and what the operator does about it."""
    if not estado.get("fonte_arp"):
        cegas.append(
            Cega(
                "arp",
                "nem /proc/net/arp nem o comando arp responderam neste sistema",
                "sem a tabela ARP não há MAC nem lista de quem está vivo; as sondas "
                "unicast varreram a faixa inteira com prazo curto",
            )
        )
    for nome, erro in (("ssdp", erro_ssdp), ("mdns", erro_mdns)):
        if erro is None:
            continue
        cegas.append(
            Cega(
                nome,
                f"o datagrama multicast foi recusado pelo sistema (errno {erro.errno}, "
                f"{erro.strerror})",
                _acao_de_multicast(),
            )
        )
    if not caminho_oui:
        cegas.append(
            Cega(
                "oui",
                "não há base de prefixos OUI instalada nesta máquina",
                "a coluna FABRICANTE fica vazia, ou traz a marca do manifesto do "
                "driver que respondeu; baixar uma base seria dado de terceiro na "
                "imagem (seção 10)",
            )
        )


def _acao_de_multicast() -> str:
    if sys.platform == "darwin":
        return (
            "Ajustes do Sistema, Privacidade e Segurança, Rede Local, e ligue a chave "
            "do aplicativo que roda este script; o padrão do sistema nega multicast a "
            "binário que não é do sistema. Se este processo estiver dentro do Docker "
            "Desktop, seção 14: rode no Mac"
        )
    return (
        "confira a rota de 224.0.0.0/4 e se este processo é dono da interface da LAN; "
        "num contêiner sem network_mode host o multicast não sai (seção 14)"
    )


def _cortar(texto: str, largura: int) -> str:
    return texto if len(texto) <= largura else texto[: largura - 1] + ">"


def _linhas_da_tabela(linhas: list[dict]) -> list[str]:
    larguras = {
        chave: min(teto, max(len(titulo), *(len(str(l[chave])) for l in linhas)))
        for titulo, chave, teto in COLUNAS
    }
    saida = ["  ".join(titulo.ljust(larguras[chave]) for titulo, chave, _ in COLUNAS).rstrip()]
    saida.append("  ".join("-" * larguras[chave] for _, chave, _ in COLUNAS))
    for linha in linhas:
        saida.append(
            "  ".join(
                _cortar(str(linha[chave]), larguras[chave]).ljust(larguras[chave])
                for _, chave, _ in COLUNAS
            ).rstrip()
        )
    return saida


def _ordenar(aparelhos: dict[str, Aparelho]) -> list[Aparelho]:
    return sorted(aparelhos.values(), key=lambda a: int(ipaddress.ip_address(a.ip)))


def _para_json(aparelho: Aparelho) -> dict:
    nome, fonte = aparelho.nome()
    return {
        "ip": aparelho.ip,
        "mac": aparelho.mac,
        "nome": nome,
        "nome_fonte": fonte,
        "fabricante": aparelho.fabricante,
        "fabricante_fonte": aparelho.fabricante_fonte,
        "drivers": sorted(aparelho.drivers),
        "identidades": {t: i for t, i in sorted(aparelho.drivers.items()) if i},
        "como": sorted(aparelho.como),
    }


def _argumentos(argv) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(
        prog="procurar",
        description=(
            "Inventory of the local segment, with the drivers this hub would use "
            "(inventário do segmento local, com os drivers que este hub usaria)"
        ),
    )
    analisador.add_argument(
        "--faixa",
        metavar="CIDR",
        default="",
        help=(
            "range to sweep; the default is read from the default interface and said "
            "out loud (faixa a varrer; o padrão é lido da interface default e dito na "
            "saída)"
        ),
    )
    analisador.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help=(
            "seconds of the listening window and of each unicast probe (segundos da "
            "janela de escuta e de cada sonda unicast); padrão 3.0"
        ),
    )
    analisador.add_argument(
        "--json",
        action="store_true",
        help=(
            "pure JSON on stdout, diagnostics on stderr (JSON puro no stdout, "
            "diagnóstico no stderr)"
        ),
    )
    analisador.add_argument(
        "--so-drivers",
        dest="so_drivers",
        action="store_true",
        help=("only the rows that matched a driver (só as linhas que casaram com um driver)"),
    )
    return analisador.parse_args(argv)


def main(argv=None) -> int:
    args = _argumentos(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(name)s: %(message)s")
    if args.timeout <= 0:
        print("procurar: --timeout tem que ser maior que zero", file=sys.stderr)
        return 2
    avisos: list[str] = []
    cegas: list[Cega] = []
    try:
        rede, interface, ip_local = _faixa(args.faixa, avisos)
    except ValueError as erro:
        print(f"procurar: {erro}", file=sys.stderr)
        return 2

    inicio = time.monotonic()
    resultado = asyncio.run(_correr(args, rede, ip_local, cegas))
    segundos = time.monotonic() - inicio

    aparelhos = _ordenar(resultado["aparelhos"])
    com_driver = [a for a in aparelhos if a.drivers]
    mostrados = com_driver if args.so_drivers else aparelhos

    # A sweep that saw nothing by multicast while the unicast path proved the
    # segment is populated is the signature and of a denied permission,
    # and saying "empty network" there would send the integrator hunting the LAN
    calado = not resultado["ssdp"] and not resultado["mdns"]
    if calado and resultado["vivos"] and not resultado["multicast_recusado"]:
        cegas.append(
            Cega(
                "ssdp e mdns",
                f"as duas varreduras multicast saíram sem erro e não tiveram "
                f"resposta, enquanto o caminho unicast achou {resultado['vivos']} "
                "hosts na LAN; é a assinatura da seção 14 e da permissão de Rede "
                "Local negada, e também é o normal num segmento sem nenhum aparelho "
                "que anuncie UPnP",
                _acao_de_multicast(),
            )
        )

    linhas = []
    for aparelho in mostrados:
        nome, _fonte = aparelho.nome()
        linhas.append(
            {
                "ip": aparelho.ip,
                "mac": aparelho.mac or "-",
                "nome": nome or "-",
                "fabricante": aparelho.fabricante or "-",
                "driver": ", ".join(sorted(aparelho.drivers)) or "-",
                "como": "+".join(sorted(aparelho.como)) or "-",
            }
        )

    rodape = _rodape(
        resultado,
        aparelhos,
        com_driver,
        mostrados,
        segundos,
        rede,
        interface,
        ip_local,
        cegas,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "faixa": str(rede),
                    "interface": interface,
                    "host_local": ip_local,
                    "segundos": round(segundos, 2),
                    "resumo": {
                        "aparelhos": len(aparelhos),
                        "com_driver": len(com_driver),
                        "mostrados": len(mostrados),
                    },
                    "sondas": {
                        "executadas": resultado["executadas"],
                        "de_driver": resultado["sondas_de_driver"],
                        "sem_sonda_unicast": resultado["sem_sonda"],
                        "cegas": [vars(c) for c in cegas],
                    },
                    "aparelhos": [_para_json(a) for a in mostrados],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        for linha in avisos + rodape:
            print(linha, file=sys.stderr)
        return 0

    for linha in avisos:
        print(f"aviso: {linha}")
    if linhas:
        for linha in _linhas_da_tabela(linhas):
            print(linha)
    else:
        print("nenhuma linha para mostrar")
    print()
    for linha in rodape:
        print(linha)
    return 0


def _rodape(
    resultado: dict,
    aparelhos: list[Aparelho],
    com_driver: list[Aparelho],
    mostrados: list[Aparelho],
    segundos: float,
    rede,
    interface: str,
    ip_local: str,
    cegas: list[Cega],
) -> list[str]:
    """The summary that says what ran, what did not, and what the numbers mean."""
    onde = f"{rede} por {interface}" if interface else str(rede)
    linhas = [
        (
            f"{len(aparelhos)} aparelhos, {len(com_driver)} com driver, "
            f"{len(mostrados)} na tabela, {segundos:.1f} s, faixa {onde}, "
            f"host local {ip_local} fora da varredura"
        ),
        "sondas: " + ", ".join(resultado["executadas"]),
        (
            "como: arp respondeu ARP, ssdp e mdns são a varredura multicast do hub, "
            "ptr se nomeou por mDNS reverso, porta respondeu a sonda unicast de um "
            "driver"
        ),
        (
            f"sondas de driver: {resultado['sondas_de_driver']} do catálogo nativo "
            f"sobre {resultado['alvos']} endereços, todas somente leitura"
        ),
    ]
    if resultado["sem_sonda"]:
        linhas.append("sem sonda unicast: " + "; ".join(resultado["sem_sonda"]))
    # The mDNS sweep of the hub asks only for the services the manifests declare,
    # so zero there is the normal answer of a segment with none of those devices, and
    # not a broken probe; saying what was asked keeps the two apart.
    linhas.append(
        f"multicast do hub: ssdp {resultado['ssdp']} achados em "
        f"{resultado['alvos_ssdp']} alvos, mdns {resultado['mdns']} achados em "
        f"{resultado['alvos_mdns']} serviços que os manifestos declaram"
    )
    if resultado["fora_da_faixa"]:
        linhas.append(
            f"{resultado['fora_da_faixa']} achados multicast responderam de fora de "
            "--faixa e ficaram fora da tabela"
        )
    if not cegas:
        linhas.append("cegas: nenhuma")
        return linhas
    linhas.append("cegas:")
    for cega in cegas:
        linhas.append(f"  {cega.sonda}: {cega.motivo}")
        linhas.append(f"    o que fazer: {cega.acao}")
    if not aparelhos:
        linhas.append(
            "a tabela está vazia, o que NÃO quer dizer que a rede está vazia: leia as "
            "sondas cegas acima antes de concluir qualquer coisa"
        )
    return linhas


if __name__ == "__main__":
    raise SystemExit(main())
