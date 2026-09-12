# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Samsung televisions before Tizen, the ones that answer a socket on 55000.

What the protocol is, so nobody has to read it again:

- One plain TCP connection on port 55000, no TLS and no cipher anywhere. Every message is the
  same shape: a zero byte, then a block with the name of the application, then a block with
  the payload; each block is its length in two little endian bytes and then the bytes.
- The first payload asks for permission and carries three base64 fields, the address of the
  remote, the identity of the remote and the name the television shows in its popup. The
  television answers granted, denied, or waiting while the popup is still on the screen, and
  it REMEMBERS the answer against the identity that asked. That is why the identity here is
  derived from the registration and never read from the machine: a MAC read from the host
  changes when the container is recreated, and the popup would come back at the customer.
- Every later payload is one KEY, and the television acknowledges NOTHING. There is no volume
  to read, no mute, no input, no title: this protocol only sends keys. So volume is not a
  capability and neither is mute, because the key of mute is a blind toggle over a state
  nobody can read back, and a button that lies is worse than a button that is absent.
- A television that is off is not on the network at all, so an open socket is what says it is
  on, and turning it back on is a Wake on LAN packet to the MAC of the television.

What is deliberately OUT: the H and J sets of 2014 and 2015, which speak another protocol on
port 8080 with a pairing and a cipher, and the sets of 2016 and later, which are the Tizen
driver of this hub.
"""

import asyncio
import base64
import hashlib
import logging
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.samsung_legado")

TIPO = "tv_samsung_legado"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
AUTH_PENDENTE = "auth_pendente"
AGUARDANDO = "aguardando"
FALHOU = "falhou"
PAREADO = "pareado"

PORTA = 55000
CONEXAO_S = 3.0
RESPOSTA_S = 3.0
# What the popup on the screen is given, and it fits inside half of a poll interval.
PAREAMENTO_S = 5.0
BLOCO_MAXIMO = 4 * 1024
FALHAS_ATE_OFFLINE = 2

APLICACAO = "iphone..iapp.samsung"
NOME_DO_CONTROLE = "QA IP Hub"

CAMPO_MAC = "mac"
PORTA_WOL = 9
_MAC = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}$")

# What the television answers to the request for permission.
CONCEDIDO = b"\x64\x00\x01\x00"
NEGADO = b"\x64\x00\x00\x00"
ESPERANDO = b"\x0a\x00\x02\x00\x00\x00"
RECUSADO = b"\x65\x00"

# The keys of the remote, in the words of the hub and in the words of the television.
TECLAS = {
    "cima": "KEY_UP",
    "baixo": "KEY_DOWN",
    "esquerda": "KEY_LEFT",
    "direita": "KEY_RIGHT",
    "ok": "KEY_ENTER",
    "voltar": "KEY_RETURN",
    "inicio": "KEY_HOME",
    "menu": "KEY_MENU",
    "guia": "KEY_GUIDE",
    "sair": "KEY_EXIT",
    "info": "KEY_INFO",
    "mais": "KEY_VOLUP",
    "menos": "KEY_VOLDOWN",
    "canal_mais": "KEY_CHUP",
    "canal_menos": "KEY_CHDOWN",
    **{f"digito_{numero}": f"KEY_{numero}" for numero in range(10)},
}
TECLA_DESLIGAR = "KEY_POWEROFF"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_TECLA = "tecla"
ACOES_DE_TRANSPORTE = {
    "tocar": "KEY_PLAY",
    "pausar": "KEY_PAUSE",
    "parar": "KEY_STOP",
    "proxima": "KEY_FF",
    "anterior": "KEY_REWIND",
}

TEXTOS = {
    "en": {
        "descricao": (
            "Samsung television before Tizen, of 2013 and earlier: power off and the keys of "
            "the remote. This protocol reads nothing back, so there is no volume level and no "
            "mute here. A set of 2016 and later is the other Samsung driver."
        ),
        "preparo": (
            "On the television: Menu, Network, and leave it on the network of the hub. Press "
            "pair here and ALLOW the popup that shows up on the screen; the television keeps "
            "that answer. Fill the MAC below and leave the network standby on, because a set "
            "that is off leaves the network and only a magic packet reaches it."
        ),
        "auth_ajuda": (
            "1. Turn the television on and press pair.\n"
            "2. A popup asks to allow the remote. Choose allow with the remote of the "
            "television.\n"
            "3. The television keeps the answer, so this is done once."
        ),
        "campo_mac": "The MAC of the television, which is what turns it on when it is off.",
        "cap_tecla": "The keys of the remote, including the two of the volume.",
    },
    "pt": {
        "descricao": (
            "Televisão Samsung anterior ao Tizen, de 2013 para trás: desligar e as teclas do "
            "controle. Este protocolo não lê nada de volta, então aqui não há nível de volume "
            "nem mudo. Um aparelho de 2016 em diante é o outro driver Samsung."
        ),
        "preparo": (
            "Na televisão: Menu, Rede, e deixe-a na rede do hub. Aperte parear aqui e PERMITA "
            "o aviso que aparece na tela; a televisão guarda essa resposta. Preencha o MAC "
            "abaixo e deixe o modo de espera em rede ligado, porque desligada ela sai da rede "
            "e só o pacote mágico chega nela."
        ),
        "auth_ajuda": (
            "1. Ligue a televisão e aperte parear.\n"
            "2. Um aviso pede para permitir o controle. Escolha permitir pelo controle da "
            "televisão.\n"
            "3. A televisão guarda a resposta, então isso é feito uma vez."
        ),
        "campo_mac": "O MAC da televisão, que é o que a liga quando ela está desligada.",
        "cap_tecla": "As teclas do controle, incluindo as duas do volume.",
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the television."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class SamsungLegado(Driver):
    """One Samsung of the generation before Tizen, allowed by a popup on its own screen."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Samsung (anterior ao Tizen)", "en": "Samsung television (pre Tizen)"},
        categoria="tv",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_TECLA, *ACOES_DE_TRANSPORTE),
        teclas=tuple(TECLAS),
        auth=Auth.POPUP_NO_APARELHO,
        config_campos=(Campo(nome=CAMPO_MAC, tipo=TipoCampo.TEXTO, obrigatorio=False),),
        # The Tizen driver claims the announcement of a Samsung, so this one is found by the
        # range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        textos=TEXTOS,
        marca="Samsung",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def autenticar(self) -> str:
        """Pareado, aguardando while the popup is on the screen, or falhou."""
        try:
            async with self._ligacao(PAREAMENTO_S):
                pass
        except _Falha as falha:
            if falha.codigo == AUTH_PENDENTE:
                return AGUARDANDO
            log.warning("%s: pairing failed with %s", self.cadastro.identidade, falha.codigo)
            return FALHOU
        log.info("%s: paired", self.cadastro.identidade)
        return PAREADO

    async def atualizar(self) -> None:
        """One poll: the connection itself, because a socket that opens and is granted is the
        only fact this protocol publishes. It is asked again every time instead of being kept,
        so a television that left the network is seen on the next poll and not on the next
        command the customer presses.
        """
        try:
            async with self._ligacao(RESPOSTA_S):
                pass
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        self._defina(online=True, ligado=True, detalhe="")

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            # The network of this television dies with it, so the only way in is the packet.
            endereco = _mac_valido(self.cadastro.campos.get(CAMPO_MAC, ""))
            if endereco is None:
                return INVALID_VALUE
            self._fio.enviado("wake on lan")
            await asyncio.to_thread(_soprar, endereco)
            return None
        if acao == ACAO_DESLIGAR:
            await self._tecla(TECLA_DESLIGAR)
            self._defina(ligado=False)
            return None
        if acao == ACAO_TECLA:
            nome = TECLAS.get(valor) if isinstance(valor, str) else None
            if nome is None:
                return INVALID_VALUE
            await self._tecla(nome)
            return None
        if acao in ACOES_DE_TRANSPORTE:
            await self._tecla(ACOES_DE_TRANSPORTE[acao])
            return None
        return NAO_SUPORTADO

    async def _tecla(self, nome: str) -> None:
        async with self._ligacao(RESPOSTA_S) as escritor:
            self._fio.enviado(f"key {nome}")
            # The television acknowledges nothing, so what leaves is the whole exchange.
            await self._escrever(escritor, b"\x00\x00\x00" + _b64(nome))

    @asynccontextmanager
    async def _ligacao(self, prazo: float) -> AsyncIterator[asyncio.StreamWriter]:
        """One connection, already granted, closed on the way out however it ends."""
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(self._endereco(), PORTA)
        except (TimeoutError, OSError) as erro:
            self._fio.falhou("connect", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.reconectou()
        try:
            await self._pedir_permissao(leitor, escritor, prazo)
            yield escritor
        finally:
            await _fechar(escritor)

    async def _pedir_permissao(
        self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter, prazo: float
    ) -> None:
        """The first payload, which is what puts the popup on the screen of the television."""
        local = escritor.get_extra_info("sockname")
        endereco = local[0] if isinstance(local, tuple) and local else "0.0.0.0"
        carga = b"\x64\x00" + _b64(endereco) + _b64(self._identidade()) + _b64(NOME_DO_CONTROLE)
        self._fio.enviado("pairing request")
        await self._escrever(escritor, carga)
        veredito = await self._ler_veredito(leitor, prazo)
        if veredito == CONCEDIDO:
            return
        if veredito == ESPERANDO:
            self._fio.recusado("pairing", AUTH_PENDENTE)
            raise _Falha(AUTH_PENDENTE)
        self._fio.recusado("pairing", ERRO_APARELHO)
        raise _Falha(ERRO_APARELHO)

    async def _ler_veredito(self, leitor: asyncio.StreamReader, prazo: float) -> bytes:
        """Reads until the television says yes or no; waiting is not an answer yet.

        A set answers the popup with two packets, one when it goes up and one when the person
        chooses, and the second one is the answer.
        """
        ultimo = b""
        try:
            async with asyncio.timeout(prazo):
                while True:
                    pacote = await _ler_pacote(leitor)
                    self._fio.recebido(pacote.hex())
                    ultimo = pacote
                    if pacote in (CONCEDIDO, NEGADO, RECUSADO):
                        return pacote
        except TimeoutError:
            return ultimo
        except (OSError, ValueError, asyncio.IncompleteReadError) as erro:
            self._fio.falhou("pairing", erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _escrever(self, escritor: asyncio.StreamWriter, carga: bytes) -> None:
        pacote = b"\x00" + _bloco(APLICACAO.encode("utf-8")) + _bloco(carga)
        try:
            escritor.write(pacote)
            await escritor.drain()
        except (OSError, ConnectionError) as erro:
            self._fio.falhou("write", erro)
            raise _Falha(EQ_OFFLINE) from erro

    def _identidade(self) -> str:
        """The identity the television keys its permission on, derived and therefore stable.

        A MAC read from the machine changes when the container is recreated, and the popup
        would come back at the customer; this one is the same for as long as the registration
        exists. The two low bits of the first byte say locally administered and not multicast,
        which is exactly what an address nobody assigned should say.
        """
        digerido = hashlib.sha256(self.cadastro.identidade.encode("utf-8")).digest()
        octetos = bytearray(digerido[:6])
        octetos[0] = (octetos[0] | 0x02) & 0xFE
        return ":".join(f"{octeto:02x}" for octeto in octetos)

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, ligado=False, detalhe=codigo)

    def _endereco(self) -> str:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco


def _bloco(dados: bytes) -> bytes:
    """The length in two little endian bytes and then the bytes, which is every block here."""
    return len(dados).to_bytes(2, "little") + dados


def _b64(texto: str) -> bytes:
    return _bloco(base64.b64encode(texto.encode("utf-8")))


async def _ler_pacote(leitor: asyncio.StreamReader) -> bytes:
    """One answer of the television: a header byte, the name it calls itself, and the verdict."""
    await leitor.readexactly(1)
    await _ler_bloco(leitor)  # The name the television calls itself, which nothing here reads.
    return await _ler_bloco(leitor)


async def _ler_bloco(leitor: asyncio.StreamReader) -> bytes:
    tamanho = int.from_bytes(await leitor.readexactly(2), "little")
    if tamanho > BLOCO_MAXIMO:
        raise ValueError("block over the ceiling")
    return await leitor.readexactly(tamanho)


async def _fechar(escritor: asyncio.StreamWriter) -> None:
    escritor.close()
    try:
        await escritor.wait_closed()
    except (OSError, ConnectionError):
        pass


def _mac_valido(bruto: str) -> bytes | None:
    limpo = bruto.strip()
    if not _MAC.match(limpo):
        return None
    return bytes.fromhex(limpo.replace(":", "").replace("-", ""))


def _soprar(endereco: bytes) -> None:
    """One magic packet to the broadcast of the segment, which is what wakes the television."""
    pacote = b"\xff" * 6 + endereco * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as soquete:
        soquete.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        soquete.sendto(pacote, ("255.255.255.255", PORTA_WOL))
