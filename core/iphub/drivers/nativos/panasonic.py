# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Panasonic Viera televisions, the ones that answer SOAP on 55000 with no PIN.

What the protocol is, so nobody has to read it again:

- Plain HTTP on port 55000, SOAP in and SOAP out, two services on two paths:
  /nrc/control_10 carries X_SendKey, which is every key of the remote and the power, and
  /dmr/control_10 is the standard UPnP renderer, which is the only absolute volume and the
  only mute that can be read back.
- The volume of the renderer is already 0 to 100, so nothing is converted here.
- There is NO reading of power, of input or of the title anywhere: a television that answers
  is on, and a television that is off stops answering, because the network of a Viera dies
  with it. That is why turning it on is a Wake on LAN packet to the MAC of the registration.
- There is no discrete input either: the protocol has one key that CYCLES the inputs, and a
  cycle cannot answer which input it landed on, so fonte is not a capability of this driver.

What is deliberately OUT: a Viera of 2018 and later asks for a PIN on the screen and then
encrypts every message with a key derived from it. That television answers this driver with
an error, the driver says so with a stable code, and the encrypted path stays out of this
image. It is a different protocol wearing the same port.
"""

import asyncio
import logging
import re
import socket
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import Cadastro, ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.panasonic")

TIPO = "tv_panasonic_viera"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA = 55000
TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

CAMINHO_NRC = "/nrc/control_10"
CAMINHO_DMR = "/dmr/control_10"
CAMINHO_DESCRICAO = "/nrc/ddd.xml"

SERVICO_NRC = "urn:panasonic-com:service:p00NetworkControl:1"
SERVICO_RENDER = "urn:schemas-upnp-org:service:RenderingControl:1"

TIPO_DO_CORPO = 'text/xml; charset="utf-8"'
ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
    ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:{acao} xmlns:u="{servico}">{dentro}</u:{acao}></s:Body></s:Envelope>'
)
CANAL = "<InstanceID>0</InstanceID><Channel>Master</Channel>"

CAMPO_MAC = "mac"
PORTA_WOL = 9
_MAC = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}$")

# What an encrypted television of 2018 and later answers with, and the name of the action it
# would want first; either one is enough to say this is the other protocol.
ERRO_DE_CIFRA = re.compile(rb"X_GetEncryptSessionId|<errorCode>600</errorCode>")

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100

# The keys of the remote, in the words of the hub and in the words of the television.
TECLAS = {
    "cima": "NRC_UP-ONOFF",
    "baixo": "NRC_DOWN-ONOFF",
    "esquerda": "NRC_LEFT-ONOFF",
    "direita": "NRC_RIGHT-ONOFF",
    "ok": "NRC_ENTER-ONOFF",
    "voltar": "NRC_RETURN-ONOFF",
    "inicio": "NRC_HOME-ONOFF",
    "menu": "NRC_MENU-ONOFF",
    "guia": "NRC_EPG-ONOFF",
    "sair": "NRC_CANCEL-ONOFF",
    "info": "NRC_INFO-ONOFF",
    "mais": "NRC_VOLUP-ONOFF",
    "menos": "NRC_VOLDOWN-ONOFF",
    "canal_mais": "NRC_CH_UP-ONOFF",
    "canal_menos": "NRC_CH_DOWN-ONOFF",
    "play_pause": "NRC_PLAY-ONOFF",
    **{f"digito_{numero}": f"NRC_D{numero}-ONOFF" for numero in range(10)},
}
TECLA_ENERGIA = "NRC_POWER-ONOFF"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_TECLA = "tecla"
ACOES_DE_TRANSPORTE = {
    "tocar": "NRC_PLAY-ONOFF",
    "pausar": "NRC_PAUSE-ONOFF",
    "parar": "NRC_STOP-ONOFF",
    "proxima": "NRC_SKIP_NEXT-ONOFF",
    "anterior": "NRC_SKIP_PREV-ONOFF",
}

TEXTOS = {
    "en": {
        "descricao": (
            "Panasonic Viera television on the local network: power, volume, mute and the keys "
            "of the remote. A Viera of 2018 and later asks for a PIN and encrypts the wire, "
            "and that model is not this driver."
        ),
        "preparo": (
            "On the television: Setup, Network, TV Remote App Settings, and leave TV Remote "
            "and Networked Standby on. Networked Standby is what lets the television be "
            "turned on again; without it, fill the MAC below so the hub can wake it."
        ),
        "campo_mac": "The MAC of the television, which is what turns it on when it is off.",
        "cap_tecla": "The keys of the remote, including the two of the volume.",
    },
    "pt": {
        "descricao": (
            "Televisão Panasonic Viera na rede local: energia, volume, mudo e as teclas do "
            "controle. Uma Viera de 2018 em diante pede um PIN e cifra o fio, e esse modelo "
            "não é este driver."
        ),
        "preparo": (
            "Na televisão: Menu, Rede, Configurações do TV Remote, e deixe o TV Remote e o "
            "modo de espera em rede ligados. O modo de espera em rede é o que permite ligá-la "
            "de novo; sem ele, preencha o MAC abaixo para o hub acordá-la."
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


class Panasonic(Driver):
    """One Panasonic Viera, commanded by SOAP and woken by a packet."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Panasonic (Viera)", "en": "Panasonic television (Viera)"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_TECLA,
            *ACOES_DE_TRANSPORTE,
        ),
        teclas=tuple(TECLAS),
        config_campos=(Campo(nome=CAMPO_MAC, tipo=TipoCampo.TEXTO, obrigatorio=False),),
        # The television announces itself as a UPnP renderer like a hundred other things do,
        # so it is found by the range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        textos=TEXTOS,
        marca="Panasonic",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def atualizar(self) -> None:
        try:
            await self._ler_estado()
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._ligar()
        if acao == ACAO_DESLIGAR:
            await self._tecla(TECLA_ENERGIA)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if not isinstance(valor, int) or isinstance(valor, bool):
                return INVALID_VALUE
            if not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
                return INVALID_VALUE
            await self._render("SetVolume", f"{CANAL}<DesiredVolume>{valor}</DesiredVolume>")
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._render("SetMute", f"{CANAL}<DesiredMute>{int(valor)}</DesiredMute>")
            self._defina(mudo=valor)
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

    async def _ligar(self) -> str | None:
        """A television still on the network takes the key; one that left takes the packet."""
        if self._estado.online:
            await self._tecla(TECLA_ENERGIA)
            self._defina(ligado=True)
            return None
        endereco = _mac_valido(self.cadastro.campos.get(CAMPO_MAC, ""))
        if endereco is None:
            return INVALID_VALUE
        self._fio.enviado("wake on lan")
        await asyncio.to_thread(_soprar, endereco)
        return None

    async def _ler_estado(self) -> None:
        """One poll: the volume and the mute of the renderer, which is all this protocol has.

        A Viera that answers is a Viera that is on, because one that is off is not on the
        network at all; there is no field to read for it.
        """
        volume = _numero(await self._render("GetVolume", CANAL), "CurrentVolume")
        mudo = _numero(await self._render("GetMute", CANAL), "CurrentMute")
        self._defina(
            online=True,
            ligado=True,
            volume=volume,
            mudo=None if mudo is None else bool(mudo),
            detalhe="",
        )

    async def _tecla(self, nome: str) -> bytes:
        return await self._soap(
            CAMINHO_NRC, SERVICO_NRC, "X_SendKey", f"<X_KeyEvent>{nome}</X_KeyEvent>"
        )

    async def _render(self, acao: str, dentro: str) -> bytes:
        return await self._soap(CAMINHO_DMR, SERVICO_RENDER, acao, dentro)

    async def _soap(self, caminho: str, servico: str, acao: str, dentro: str) -> bytes:
        url = f"http://{self._endereco()}:{PORTA}{caminho}"
        mensagem = ENVELOPE.format(acao=acao, servico=servico, dentro=dentro)
        rotina = acao.startswith("Get")
        self._fio.enviado(f"{acao} {dentro}", rotina=rotina)
        sessao = await self._abrir()
        try:
            async with sessao.post(
                url,
                data=mensagem.encode("utf-8"),
                headers={"Content-Type": TIPO_DO_CORPO, "SOAPACTION": f'"{servico}#{acao}"'},
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(acao, erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado == 200:
            return bruto
        if ERRO_DE_CIFRA.search(bruto):
            # This is the television of 2018 and later, which wants a PIN and a cipher.
            self._fio.recusado(acao, NAO_SUPORTADO)
            raise _Falha(NAO_SUPORTADO)
        self._fio.recusado(acao, ERRO_APARELHO)
        raise _Falha(ERRO_APARELHO)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

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

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The UDN of the description, which is the one stable name a Viera publishes."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        url = f"http://{endereco}:{PORTA}{CAMINHO_DESCRICAO}"
        try:
            async with (
                ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao,
                sessao.get(url, allow_redirects=False) as resposta,
            ):
                if resposta.status != 200:
                    return None
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
        except (TimeoutError, ClientError, OSError, ValueError):
            return None
        return _achar(bruto, "UDN") or None


def _achar(bruto: bytes, nome: str) -> str:
    """The text of the first tag with this name, whatever namespace it wears."""
    # A DTD is what an entity bomb needs, and a television never sends one.
    if not bruto or re.search(rb"<!(?:DOCTYPE|ENTITY)", bruto, re.IGNORECASE):
        return ""
    try:
        raiz = ElementTree.fromstring(bruto)
    except (ElementTree.ParseError, ValueError):
        return ""
    for elemento in raiz.iter():
        if elemento.tag.rpartition("}")[2] == nome:
            return (elemento.text or "").strip()
    return ""


def _numero(bruto: bytes, nome: str) -> int | None:
    texto = _achar(bruto, nome)
    return int(texto) if texto.isdigit() else None


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
