# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Arcam receivers over the binary control of the receiver itself.

What the protocol is, so nobody has to read it again:

- One TCP connection on port 50000, and every message is a FRAME of bytes: 0x21 opens it,
  0x0D closes it, and between them come the zone, the command, the length and the data. A
  question is the same frame with one byte of data, 0xF0, which is what the protocol calls
  a request.
- An answer carries one byte more than a command: after the command comes the ANSWER code,
  where zero means the rest of the frame is the state and anything else is the receiver
  refusing, and each refusal has its own meaning (a zone it does not have, a command it does
  not know, a value it does not take, a command it cannot obey right now).
- The closing byte is NOT a separator: 0x0D is also the command of the volume, so a reader
  that split on it would cut the answer of that very command in half. A frame is read by its
  LENGTH: five bytes of header, the data the length byte announces, and the closing byte.
- Power reads one for on, and MUTE reads ZERO for muted, which is backwards from every other
  byte of this protocol and is what it says.
- The volume is a whole number from zero to ninety nine, so it is converted to the zero to a
  hundred of the contract and back.
- The inputs are bytes of a table of the model, and what travels is the NAME of the input,
  because a scene that says BD reads as BD and not as three.
- A zone of the receiver is a registration of its own, which is the zone byte of every frame.
"""

import asyncio
import logging

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.arcam")

TIPO = "receiver_arcam"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA = 50000
CONEXAO_S = 3.0
RESPOSTA_S = 2.0
QUADROS_ATE_DESISTIR = 8
QUADRO_MAXIMO = 512
FALHAS_ATE_OFFLINE = 2

ABRE = 0x21
FECHA = 0x0D
PERGUNTA = 0xF0

CAMPO_ZONA = "zona"
ZONA_PADRAO = 1
ZONA_MAXIMA = 2

CMD_ENERGIA = 0x00
CMD_VOLUME = 0x0D
CMD_MUDO = 0x0E
CMD_FONTE = 0x1D

RESPOSTA_OK = 0x00
RECUSAS = {
    0x82: "zone it does not have",
    0x83: "command it does not know",
    0x84: "value it does not take",
    0x85: "command it cannot obey now",
    0x86: "length it does not accept",
}

VOLUME_MAXIMO = 99

# The inputs of a receiver of this line, as the byte of each one.
FONTES = {
    "CD": 0x01,
    "BD": 0x02,
    "AV": 0x03,
    "SAT": 0x04,
    "PVR": 0x05,
    "VCR": 0x06,
    "AUX": 0x08,
    "DISPLAY": 0x09,
    "FM": 0x0B,
    "DAB": 0x0C,
    "NET": 0x0E,
    "USB": 0x0F,
    "STB": 0x10,
    "GAME": 0x11,
    "PHONO": 0x12,
}
POR_BYTE = {byte: nome for nome, byte in FONTES.items()}

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"

TEXTOS = {
    "en": {
        "descricao": (
            "Arcam receiver over the binary control of the receiver, with no cloud and no "
            "account: power, volume, mute and the input. One registration is ONE ZONE."
        ),
        "preparo": (
            "On the receiver: General Setup, Network, and leave the network control on. On "
            "the models that have the setting, leave the standby mode on the one that keeps "
            "the network alive, or the receiver stops answering when it is off."
        ),
        "campo_zona": "The zone of the receiver, 1 or 2. One zone is one registration.",
        "cap_volume": "Converted from the zero to ninety nine of the receiver.",
    },
    "pt": {
        "descricao": (
            "Receiver Arcam pelo controle binário do próprio receiver, sem nuvem e sem conta: "
            "energia, volume, mudo e a entrada. Um cadastro é UMA ZONA."
        ),
        "preparo": (
            "No receiver: General Setup, Network, e deixe o controle por rede ligado. Nos "
            "modelos que têm a opção, deixe o modo de standby no que mantém a rede viva, "
            "senão o receiver para de responder quando está desligado."
        ),
        "campo_zona": "A zona do receiver, 1 ou 2. Uma zona é um cadastro.",
        "cap_volume": "Convertido do zero a noventa e nove do receiver.",
    },
}

SUGESTOES = (
    Sugestao("entradas", "Blu-ray", "BD"),
    Sugestao("entradas", "Satelite", "SAT"),
    Sugestao("entradas", "Rede", "NET"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the receiver."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Arcam(Driver):
    """One zone of an Arcam receiver, asked and commanded one frame at a time."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Arcam", "en": "Arcam receiver"},
        categoria="receiver",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_VOLUME, ACAO_MUDO, ACAO_FONTE),
        config_campos=(Campo(nome=CAMPO_ZONA, tipo=TipoCampo.INTEIRO, padrao=str(ZONA_PADRAO)),),
        # The receiver announces nothing this hub can claim, so it is found by the range
        # sweep, which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Arcam",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._leitor: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        await self._descartar()

    async def atualizar(self) -> None:
        try:
            await self._ler_estado()
        except _Falha as falha:
            await self._descartar()
            self._falhar(falha.codigo)
            return
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                await self._descartar()
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            await self._trocar(CMD_ENERGIA, bytes([0x01]))
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._trocar(CMD_ENERGIA, bytes([0x00]))
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._trocar(CMD_VOLUME, bytes([_para_o_aparelho(valor)]))
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            # Zero is muted on this command, which is backwards from every other byte here.
            await self._trocar(CMD_MUDO, bytes([0x00 if valor else 0x01]))
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            byte = _byte_da_fonte(valor)
            if byte is None:
                return INVALID_VALUE
            await self._trocar(CMD_FONTE, bytes([byte]))
            self._defina(fonte=POR_BYTE[byte])
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: the four questions of the zone, each one a frame of its own."""
        energia = await self._trocar(CMD_ENERGIA, bytes([PERGUNTA]), rotina=True)
        volume = await self._trocar(CMD_VOLUME, bytes([PERGUNTA]), rotina=True)
        mudo = await self._trocar(CMD_MUDO, bytes([PERGUNTA]), rotina=True)
        fonte = await self._trocar(CMD_FONTE, bytes([PERGUNTA]), rotina=True)
        self._defina(
            online=True,
            ligado=_bit(energia) == 1,
            volume=_do_aparelho(_bit(volume)),
            # Zero is muted, which is the one byte of this protocol that reads backwards.
            mudo=None if _bit(mudo) is None else _bit(mudo) == 0,
            fonte=POR_BYTE.get(_bit(fonte)),
            fontes=tuple(FONTES),
            detalhe="",
        )

    async def _trocar(self, comando: int, dados: bytes, *, rotina: bool = False) -> bytes:
        """One frame out and the frame of THAT command back, anything else dropped."""
        leitor, escritor = await self._conectar()
        zona = self._zona()
        quadro = bytes([ABRE, zona, comando, len(dados), *dados, FECHA])
        self._fio.enviado(quadro.hex(), rotina=rotina)
        try:
            escritor.write(quadro)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(quadro.hex(), erro)
            raise _Falha(EQ_OFFLINE) from erro
        for _ in range(QUADROS_ATE_DESISTIR):
            lido = await self._ler_quadro()
            self._fio.recebido(lido.hex(), rotina=rotina)
            if lido[1] != zona or lido[2] != comando:
                # The receiver pushes the state of another zone or another command when
                # somebody touches the front panel, and that is not the answer of this one.
                log.debug("%s: frame of the receiver dropped: %s", self._id(), lido.hex())
                continue
            resposta = lido[3]
            if resposta != RESPOSTA_OK:
                self._fio.recusado(quadro.hex(), INVALID_VALUE)
                log.warning(
                    "%s: the receiver refused command %#04x: %s",
                    self._id(),
                    comando,
                    RECUSAS.get(resposta, "reason it did not name"),
                )
                raise _Falha(INVALID_VALUE)
            tamanho = lido[4]
            return lido[5 : 5 + tamanho]
        raise _Falha(ERRO_APARELHO)

    async def _ler_quadro(self) -> bytes:
        """One answer frame, read by the length it declares and never by its closing byte.

        The closing byte is 0x0D and so is the command of the volume, so a reader that split
        on it would cut the answer of that command in half. What is read is the header, then
        exactly the data the header announces, then the byte that closes it.
        """
        leitor, _escritor = await self._conectar()
        try:
            async with asyncio.timeout(RESPOSTA_S):
                while True:
                    inicio = await leitor.readexactly(1)
                    if inicio[0] != ABRE:
                        # A byte outside a frame is a stream out of step, and the only way
                        # back in is the byte that opens one.
                        log.debug("%s: byte outside a frame dropped", self._id())
                        continue
                    cabeca = await leitor.readexactly(4)
                    dados = await leitor.readexactly(cabeca[3])
                    fim = await leitor.readexactly(1)
                    if fim[0] != FECHA:
                        raise _Falha(ERRO_APARELHO)
                    return inicio + cabeca + dados + fim
        except TimeoutError:
            self._fio.falhou("read", "no answer")
            raise _Falha(ERRO_APARELHO) from None
        except (OSError, asyncio.IncompleteReadError, ValueError) as erro:
            self._fio.falhou("read", erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _conectar(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        leitor, escritor = self._leitor, self._escritor
        if leitor is not None and escritor is not None and not escritor.is_closing():
            return leitor, escritor
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(
                    endereco, PORTA, limit=QUADRO_MAXIMO
                )
        except (OSError, TimeoutError) as erro:
            self._fio.falhou("connect", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._leitor, self._escritor = leitor, escritor
        self._fio.reconectou()
        return leitor, escritor

    async def _descartar(self) -> None:
        escritor = self._escritor
        self._leitor = self._escritor = None
        if escritor is None or escritor.is_closing():
            return
        escritor.close()
        try:
            await escritor.wait_closed()
        except (OSError, RuntimeError, TimeoutError, asyncio.CancelledError):
            log.debug("%s: the socket did not close cleanly", self._id())

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, detalhe=codigo)

    def _zona(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_ZONA, "").strip()
        if not bruto.isdigit():
            return ZONA_PADRAO
        zona = int(bruto)
        return zona if 1 <= zona <= ZONA_MAXIMA else ZONA_PADRAO

    def _id(self) -> str:
        return self.cadastro.identidade


def _bit(dados: bytes) -> int | None:
    return dados[0] if dados else None


def _do_aparelho(bruto: int | None) -> int | None:
    if bruto is None:
        return None
    return max(0, min(100, round(bruto * 100 / VOLUME_MAXIMO)))


def _para_o_aparelho(valor: int) -> int:
    return max(0, min(VOLUME_MAXIMO, round(valor * VOLUME_MAXIMO / 100)))


def _byte_da_fonte(valor: object) -> int | None:
    """The byte of the input, from the name of the table of this line."""
    if not isinstance(valor, str):
        return None
    return FONTES.get(valor.strip().upper())
