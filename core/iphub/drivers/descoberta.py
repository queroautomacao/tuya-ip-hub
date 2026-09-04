# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Discovery generated from the manifests: an SSDP sweep that believes only the sender.

Section 6: the plan is built from the manifests, never written by hand, and two manifests
that claim the same signature are a test error, not a decision taken in runtime. Section 9
in spirit: an answer is data, so the address of a device is the address the datagram came
from and never what the answer points at.

mDNS: procurar_mdns asks for the services the manifests declare, one shot over UDP the way
RFC 6762 defines a query that leaves an ephemeral port, with no background browser and no
dependency. The rule of the address holds there too: an A record that names a host other
than the sender is read and refused, never followed.

Descoberta gerada dos manifestos: uma varredura SSDP que acredita apenas no remetente.

Seção 6: o plano nasce dos manifestos, nunca escrito à mão, e dois manifestos que
reivindicam a mesma assinatura são erro de teste, não decisão tomada em runtime. Seção 9
em espírito: uma resposta é dado, então o endereço de um aparelho é o endereço de onde o
datagrama veio e nunca o que a resposta aponta.

mDNS: procurar_mdns pergunta pelos serviços que os manifestos declaram, um tiro só sobre
UDP como a RFC 6762 define uma consulta que sai de porta efêmera, sem navegador de fundo e
sem dependência. A regra do endereço vale ali também: um registro A que nomeia outro host
que não o remetente é lido e recusado, nunca seguido.
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

# mDNS, RFC 6762. A query that leaves an ephemeral port is a one shot query, so the answer
# comes back by unicast and this daemon needs neither the multicast group nor a browser.
# mDNS, RFC 6762. Uma consulta que sai de porta efêmera é consulta de um tiro só, então a
# resposta volta por unicast e este daemon não precisa nem do grupo multicast nem de um
# navegador de fundo.
DESTINO_MDNS = ("224.0.0.251", 5353)
SUFIXO_MDNS = ".local"

CABECALHO_DNS = 12
RESPOSTA_DNS = 0x8000
PONTEIRO_DNS = 0xC0
ROTULO_DO_PONTEIRO = 0x3F
CLASSE_IN = 1
# Why: RFC 6762 sets the cache flush bit on the class of a unique record, so a real SRV and a
# real A arrive with class 0x8001 and only the low bits name the class.
# Por que: a RFC 6762 liga o bit de limpeza de cache na classe de um registro único, então um
# SRV e um A reais chegam com classe 0x8001 e só os bits baixos nomeiam a classe.
MASCARA_CLASSE = 0x7FFF
TIPO_A = 1
TIPO_PTR = 12
TIPO_SRV = 33
TAMANHO_A = 4
TAMANHO_SRV_MINIMO = 7
CABECALHO_REGISTRO = 10
PORTA_MAXIMA = 65535

# Why: a name that walks in circles and a message that claims ten thousand records are the
# two ways a datagram makes a reader work forever; each one meets a number here.
# Por que: um nome que anda em círculo e uma mensagem que declara dez mil registros são os
# dois jeitos de um datagrama fazer um leitor trabalhar para sempre; cada um encontra um
# número aqui.
ROTULO_MAXIMO = 63
NOME_MAXIMO = 255
SALTOS_MAXIMOS = 16
REGISTROS_MAXIMOS = 64
INSTANCIAS_MAXIMAS = 32

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

    mdns lists the services the manifests declare, in the form the manifest wrote them;
    procurar_mdns is what turns each one into a question on the wire.

    O que buscar e como ler a resposta, gerado a partir dos manifestos.

    mdns lista os serviços que os manifestos declaram, na forma em que o manifesto os
    escreveu; procurar_mdns é quem transforma cada um numa pergunta no fio.
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

    Uma consulta PTR por serviço declarado, depois toda resposta antes de timeout_s, dobrada
    por nome de instância.
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


async def _perguntar(
    perguntas: tuple[bytes, ...],
    coleta: "_Coleta | _ColetaMdns",
    *,
    destino: tuple[str, int],
    timeout_s: float,
    bind: tuple[str, int],
) -> None:
    """Puts every question of one search on one socket and hands the answers to the coleta.

    Põe toda pergunta de uma busca num socket só e entrega as respostas à coleta.
    """
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
            # Why: an unreachable host on the segment surfaces on the next receive, and it is
            # not a reason to abandon a sweep that other devices are still answering.
            # Por que: um host inalcançável no segmento aparece na leitura seguinte, e não é
            # razão para abandonar uma varredura que outros aparelhos ainda respondem.
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
        # Why: a sweep cut short leaves a device off the list, and the reason has to be in
        # the log. Por que: uma varredura cortada deixa aparelho fora, e a razão vai ao log.
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


def _servicos_mdns(plano: Plano) -> dict[str, str]:
    """The services to ask for, in the form the wire uses, each pointing at the tipo that
    claims it.

    Os serviços a perguntar, na forma que o fio usa, cada um apontando o tipo que o pede.
    """
    servicos: dict[str, str] = {}
    for declarado, tipo in plano.mdns.items():
        nome = _nome_de_servico(declarado)
        # Why: a manifest is data on disk, and a label longer than the wire admits would go
        # out as a malformed question that no device on the segment can answer.
        # Por que: um manifesto é dado em disco, e um rótulo maior do que o fio admite sairia
        # como pergunta torta que nenhum aparelho do segmento sabe responder.
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
    """A name in the length prefixed form of DNS, or None when it does not fit the wire.

    Um nome na forma de prefixo de tamanho do DNS, ou None quando não cabe no fio.
    """
    saida = bytearray()
    for parte in nome.split("."):
        bruto = parte.encode("utf-8", errors="ignore")
        if not bruto or len(bruto) > ROTULO_MAXIMO:
            return None
        saida += bytes([len(bruto)]) + bruto
    saida += b"\x00"
    return bytes(saida) if len(saida) <= NOME_MAXIMO else None


def _pergunta_mdns(nome: str) -> bytes | None:
    """One PTR question for one service, which is the whole query this daemon sends.

    Uma pergunta PTR por serviço, que é toda a consulta que este daemon envia.
    """
    codificado = _nome_em_bytes(nome)
    if codificado is None:
        return None
    # Why: RFC 6762 fixes the identifier of a query at zero, so it is not a token to check in
    # the answer, and the ceilings below are what bound a hostile one.
    # Por que: a RFC 6762 fixa em zero o identificador de uma consulta, então ele não é
    # segredo a conferir na resposta, e os tetos abaixo é que limitam uma hostil.
    cabecalho = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    return cabecalho + codificado + TIPO_PTR.to_bytes(2, "big") + CLASSE_IN.to_bytes(2, "big")


def _nome(dados: bytes, posicao: int) -> tuple[tuple[bytes, ...], int] | None:
    """The labels of a name and the position right after it, or None for a name that lies.

    Os rótulos de um nome e a posição logo depois dele, ou None para um nome que mente.
    """
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
            # Why: a pointer that does not go strictly backwards is how a datagram makes a
            # reader walk in circles, and a daemon walking in circles is a daemon that is
            # down. Por que: um ponteiro que não anda estritamente para trás é como um
            # datagrama faz um leitor andar em círculo, e um daemon andando em círculo é um
            # daemon fora do ar.
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

    Todo registro de uma resposta como (nome, tipo, onde o dado começa, que tamanho tem).

    None é um datagrama que não é resposta mDNS ou que não se lê inteiro; o que uma mensagem
    declara além de REGISTROS_MAXIMOS fica no fio.
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
    """What one answer said about one instance, before it becomes an Achado.

    O que uma resposta disse de uma instância, antes de ela virar um Achado.
    """

    servico: str
    rotulo: str
    porta: int | None = None
    alvo: str | None = None


def _ler_mdns(dados: bytes, ip: str, servicos: dict[str, str]) -> tuple[tuple[str, Achado], ...]:
    """What one answer says about the instances of the declared services, by instance name.

    O que uma resposta diz das instâncias dos serviços declarados, por nome de instância.
    """
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
    """Files one record under the instance it names, and only for a service we asked for.

    Arquiva um registro sob a instância que ele nomeia, e só para um serviço que pedimos.
    """
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
    # Why: the instance name carries its own service, so a PTR and an SRV that arrive in
    # different datagrams still name the same tipo without one having to trust the other.
    # Por que: o nome da instância carrega o próprio serviço, então um PTR e um SRV que
    # chegam em datagramas diferentes ainda nomeiam o mesmo tipo sem um confiar no outro.
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
    """The finding of one instance: the ip of the A record, and the port of the SRV.

    O achado de uma instância: o ip do registro A, e a porta do SRV.
    """
    apontado = enderecos.get(instancia.alvo) if instancia.alvo else None
    porta = instancia.porta
    if apontado is not None and apontado != ip:
        # Why: section 9. Following the address an answer points at is how this hub becomes a
        # proxy for the LAN of the customer, so an answer that names another host keeps only
        # what it said about itself. Por que: seção 9. Seguir o endereço que uma resposta
        # aponta é como este hub vira proxy da LAN do cliente, então uma resposta que nomeia
        # outro host guarda só o que disse de si.
        log.warning(
            "mdns answer from %s points instance %r at %s; the address is not followed",
            ip,
            nome,
            apontado,
        )
        apontado, porta = None, None
    return nome, Achado(
        tipo=servicos[instancia.servico],
        # Why: section 6 makes an identity a uuid, a mac or a serial, and an instance name is
        # none of the three; the driver reads the real identity when the equipment is
        # registered. Por que: a seção 6 faz de uma identidade um uuid, um mac ou um serial, e
        # um nome de instância não é nenhum dos três; o driver lê a identidade real quando o
        # equipamento é cadastrado.
        identidade="",
        ip=apontado or ip,
        porta=porta if porta is not None and 0 < porta <= PORTA_MAXIMA else None,
        descricao=instancia.rotulo,
    )


def _texto_do_rotulo(rotulo: bytes) -> str:
    return rotulo.decode("utf-8", errors="replace")


def _texto_do_nome(rotulos: tuple[bytes, ...]) -> str:
    # Why: DNS compares names without case, so the comparison here does the same or an
    # instance of "_LinkPlay._tcp.local" would answer for nobody.
    # Por que: o DNS compara nomes sem caixa, então a comparação aqui faz o mesmo ou uma
    # instância de "_LinkPlay._tcp.local" não responderia por ninguém.
    return ".".join(_texto_do_rotulo(rotulo) for rotulo in rotulos).lower()


@dataclass
class _ColetaMdns:
    """The answer of an mDNS query while it is built: one entry per instance of one sender.

    A resposta de uma consulta mDNS enquanto nasce: uma entrada por instância de um remetente.
    """

    servicos: dict[str, str]
    por_chave: dict[tuple[str, str], Achado] = field(default_factory=dict)

    def absorver(self, dados: bytes, ip: str) -> None:
        for nome, achado in _ler_mdns(dados, ip, self.servicos):
            # Why: a device answers the PTR in one datagram and the SRV with the A in the
            # next, so the fold is by instance name and never by datagram.
            # Por que: um aparelho responde o PTR num datagrama e o SRV com o A no seguinte,
            # então a dobra é por nome de instância e nunca por datagrama.
            anterior = self.por_chave.get((ip, nome))
            self.por_chave[(ip, nome)] = achado if anterior is None else _mesclar(anterior, achado)

    def quantos(self) -> int:
        return len(self.por_chave)

    def resultado(self) -> tuple[Achado, ...]:
        return tuple(sorted(self.por_chave.values(), key=lambda a: (a.ip, a.tipo, a.descricao)))
