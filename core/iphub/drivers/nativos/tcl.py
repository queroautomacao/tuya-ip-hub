# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""TCL televisions of the line that is NOT Android and NOT Roku.

Most of what TCL sells carries somebody else's platform, and each one already has its driver
here: a TCL with Google TV is the Android television driver, which speaks the remote protocol
of the platform, and a TCL Roku TV is the Roku driver. This one is for the line in between,
the sets with the TCL system of their own, and the same protocol is reported to answer on
some Thomson sets, which is the other brand of the same house.

What the protocol is, so nobody has to read it again:

- one plain TCP connection to port 4123, and ONE message per key:
  <?xml version="1.0" encoding="utf-8"?><root><action name="setKey" eventAction="K"
  keyCode="K" /></root>, where K is a word of the remote such as TR_KEY_HOME;
- the television answers NOTHING. Not an acknowledgement, not a state, not an error: there is
  no volume to read, no mute, no input and no power. So this driver declares keys and the two
  ends of the power, and declares no volume and no mute, because a level and a toggle nobody
  can read back are buttons that lie;
- there is no way to CHOOSE an input either. The published way is a blind dance of keys with
  pauses (exit, mute, tv, source, N times down, ok, mute) that mutes the television on its
  way through, and a macro that walks a menu by counting is a macro that lands somewhere else
  the day the menu changes. It is not implemented, and fonte is not a capability;
- a set in standby leaves the network, so a connection that opens is what says it is on, and
  turning it back on is a Wake on LAN packet to the MAC of the registration.

The protocol was read from the library the HACS component of TCL uses, which sends exactly
the message above to that port.
"""

import asyncio
import logging
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.tcl")

TIPO = "tv_tcl"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"

PORTA = 4123
CONEXAO_S = 3.0
FALHAS_ATE_OFFLINE = 2

CABECALHO = '<?xml version="1.0" encoding="utf-8"?>'
MENSAGEM = (
    '{cabecalho}<root><action name="setKey" eventAction="{tecla}" keyCode="{tecla}" /></root>'
)

CAMPO_MAC = "mac"
PORTA_WOL = 9
_MAC = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}$")

# The keys of the remote, in the words of the hub and in the words of the television. Only
# the words of the published list travel, so nothing this hub sends is invented.
TECLAS = {
    "cima": "TR_KEY_UP",
    "baixo": "TR_KEY_DOWN",
    "esquerda": "TR_KEY_LEFT",
    "direita": "TR_KEY_RIGHT",
    "ok": "TR_KEY_OK",
    "voltar": "TR_KEY_BACK",
    "inicio": "TR_KEY_HOME",
    "menu": "TR_KEY_MAINMENU",
    "guia": "TR_KEY_GUIDE",
    "sair": "TR_KEY_EXIT",
    "info": "TR_KEY_INFOWINDOW",
    "mais": "TR_KEY_VOL_UP",
    "menos": "TR_KEY_VOL_DOWN",
    "canal_mais": "TR_KEY_CH_UP",
    "canal_menos": "TR_KEY_CH_DOWN",
    "play_pause": "TR_KEY_PLAYPAUSE",
    "proxima": "TR_KEY_NEXT",
    "anterior": "TR_KEY_PREVIOUS",
    **{f"digito_{numero}": f"TR_KEY_{numero}" for numero in range(10)},
}
TECLA_ESPERA = "TR_KEY_SUSPEND"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_TECLA = "tecla"
# The set has ONE key for play and pause, so the two are the same word of the vocabulary and
# never two capabilities that would each claim to know where the film is.
ACOES_DE_TRANSPORTE = {"proxima": "TR_KEY_NEXT", "anterior": "TR_KEY_PREVIOUS"}

TEXTOS = {
    "en": {
        "descricao": (
            "TCL television of the line with the system of TCL itself, and some Thomson sets "
            "of the same house: power and the keys of the remote. A TCL with Google TV is the "
            "Android television driver and a TCL Roku TV is the Roku driver, and both of those "
            "do far more than this protocol can."
        ),
        "preparo": (
            "On the television: Network, and leave it on the network of the hub. Nothing is "
            "paired and nothing is enabled, because this protocol asks for no credential and "
            "answers nothing. Fill the MAC below: a set in standby leaves the network and only "
            "a magic packet reaches it."
        ),
        "campo_mac": "The MAC of the television, which is what turns it on when it is off.",
        "cap_tecla": "The keys of the remote, including the two of the volume.",
    },
    "pt": {
        "descricao": (
            "Televisão TCL da linha com o sistema da própria TCL, e alguns aparelhos Thomson "
            "da mesma casa: energia e as teclas do controle. Uma TCL com Google TV usa o "
            "driver de televisão Android e uma TCL Roku TV usa o driver Roku, e os dois fazem "
            "muito mais do que este protocolo permite."
        ),
        "preparo": (
            "Na televisão: Rede, e deixe-a na rede do hub. Nada é pareado e nada é habilitado, "
            "porque este protocolo não pede credencial e não responde nada. Preencha o MAC "
            "abaixo: em espera a televisão sai da rede e só o pacote mágico chega nela."
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


class Tcl(Driver):
    """One TCL of the line of its own, commanded by one message per key."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV TCL (sistema TCL)", "en": "TCL television (TCL system)"},
        categoria="tv",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_TECLA, *ACOES_DE_TRANSPORTE),
        teclas=tuple(TECLAS),
        config_campos=(Campo(nome=CAMPO_MAC, tipo=TipoCampo.TEXTO, obrigatorio=False),),
        # The television announces nothing of its own on this port, so it is found by the
        # range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        textos=TEXTOS,
        marca="TCL",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def atualizar(self) -> None:
        """One poll: the connection itself, which is the only fact this protocol publishes.

        It is opened again every time instead of being kept, so a television that left the
        network is seen on the next poll and not on the next command the customer presses.
        """
        try:
            async with self._ligacao():
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
            await self._tecla(TECLA_ESPERA)
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
        async with self._ligacao() as escritor:
            self._fio.enviado(f"key {nome}")
            # The television acknowledges nothing, so what leaves here is the whole exchange.
            escritor.write(MENSAGEM.format(cabecalho=CABECALHO, tecla=nome).encode("utf-8"))
            try:
                await escritor.drain()
            except (OSError, ConnectionError) as erro:
                self._fio.falhou(f"key {nome}", erro)
                raise _Falha(EQ_OFFLINE) from erro

    @asynccontextmanager
    async def _ligacao(self) -> AsyncIterator[asyncio.StreamWriter]:
        """One connection, closed on the way out however it ends."""
        try:
            async with asyncio.timeout(CONEXAO_S):
                _leitor, escritor = await asyncio.open_connection(self._endereco(), PORTA)
        except (TimeoutError, OSError) as erro:
            self._fio.falhou("connect", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.reconectou()
        try:
            yield escritor
        finally:
            escritor.close()
            try:
                await escritor.wait_closed()
            except (OSError, ConnectionError):
                pass

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
