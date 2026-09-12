# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""NAD amplifiers over IP, which is two protocols and not one.

What the protocols are, so nobody has to read it again:

- The line the receivers of the AV series speak is TEXT on port 23: `Main.Power=On` sets it,
  `Main.Power?` asks it, and the amplifier answers the same sentence with the value it landed
  on. The terminator is a carriage return. The volume is in decibels, and the floor and the
  ceiling change with the model, so both are fields of the registration.
- The line the network amplifiers speak is BINARY on port 50001: every message is a short
  string of bytes and every answer is ten bytes whose LAST one carries the value. The volume
  there is a whole number from zero to two hundred and the inputs are a fixed table of that
  line. Nothing in it is text.
- Which one an amplifier speaks is a fact of the model and not something either protocol
  answers, so it is a field of the registration and the driver speaks the one it is told.
"""

import asyncio
import logging
import re

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.nad")

TIPO = "amplificador_nad"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PROTOCOLO_TEXTO = "texto"
PROTOCOLO_BINARIO = "binario"
PROTOCOLOS = (PROTOCOLO_TEXTO, PROTOCOLO_BINARIO)

PORTA_TEXTO = 23
PORTA_BINARIA = 50001
CONEXAO_S = 3.0
RESPOSTA_S = 2.0
LINHAS_ATE_DESISTIR = 8
LEITURA_MAXIMA = 1024
FALHAS_ATE_OFFLINE = 2

FIM_DA_LINHA = b"\r"

CAMPO_PROTOCOLO = "protocolo"
CAMPO_DB_MINIMO = "db_minimo"
CAMPO_DB_MAXIMO = "db_maximo"

DB_MINIMO_PADRAO = -80.0
DB_MAXIMO_PADRAO = 0.0

# What the text protocol calls each thing, which is also the name it answers with.
TEXTO_ENERGIA = "Main.Power"
TEXTO_VOLUME = "Main.Volume"
TEXTO_MUDO = "Main.Mute"
TEXTO_FONTE = "Main.Source"
TEXTO_MODELO = "Main.Model"
LIGADO = "On"
DESLIGADO = "Off"

# The binary protocol: a question, and an answer of ten bytes whose last one is the value.
BIN_PERGUNTA_VOLUME = bytes.fromhex("0001020204")
BIN_PERGUNTA_ENERGIA = bytes.fromhex("0001020209")
BIN_PERGUNTA_MUDO = bytes.fromhex("000102020a")
BIN_PERGUNTA_FONTE = bytes.fromhex("0001020203")
BIN_LIGAR = bytes.fromhex("0001020901")
BIN_ECONOMIA = bytes.fromhex("00010207000001020207")
BIN_DESLIGAR = bytes.fromhex("0001020900")
BIN_VOLUME = bytes.fromhex("00010204")
BIN_MUDO = bytes.fromhex("0001020a01")
BIN_SEM_MUDO = bytes.fromhex("0001020a00")
BIN_FONTE = bytes.fromhex("00010203")
BIN_RESPOSTA = 10
BIN_VOLUME_MAXIMO = 200

# The inputs of the line that speaks binary are a fixed table of it, not something it answers.
BIN_FONTES = {
    "00": "Coaxial 1",
    "01": "Coaxial 2",
    "02": "Optical 1",
    "03": "Optical 2",
    "04": "Computer",
    "05": "Airplay",
    "06": "Dock",
    "07": "Bluetooth",
}

_RESPOSTA_DE_TEXTO = re.compile(r"^(?P<nome>[A-Za-z0-9.]+)=(?P<valor>.*)$")
_NUMERO = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_PALAVRA = re.compile(r"^[A-Za-z0-9 ._-]{1,32}$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"

TEXTOS = {
    "en": {
        "descricao": (
            "NAD amplifier over IP. Which protocol the amplifier speaks is a fact of the "
            "model: the receivers of the AV series speak text on port 23 and the network "
            "amplifiers speak the binary one on port 50001."
        ),
        "preparo": (
            "On the amplifier: the network module has to be enabled, and on the models that "
            "have the setting, control over IP has to be left on in standby, or the amplifier "
            "stops answering when it is off."
        ),
        "campo_protocolo": (
            "text for the receivers of the AV series, binary for the network amplifiers. The "
            "amplifier does not answer which one it speaks."
        ),
        "campo_db_minimo": "The floor of the volume in decibels, only used by the text protocol.",
        "campo_db_maximo": (
            "The ceiling of the volume in decibels, only used by the text protocol."
        ),
        "cap_volume": (
            "Converted from the decibels of the text protocol or from the zero to two hundred "
            "of the binary one."
        ),
    },
    "pt": {
        "descricao": (
            "Amplificador NAD por IP. Qual protocolo o amplificador fala é fato do modelo: os "
            "receivers da série AV falam texto na porta 23 e os amplificadores de rede falam "
            "o binário na porta 50001."
        ),
        "preparo": (
            "No amplificador: o módulo de rede precisa estar habilitado, e nos modelos que têm "
            "a opção o controle por IP precisa ficar ligado em standby, senão o amplificador "
            "para de responder quando está desligado."
        ),
        "campo_protocolo": (
            "texto para os receivers da série AV, binario para os amplificadores de rede. O "
            "amplificador não responde qual dos dois ele fala."
        ),
        "campo_db_minimo": "O piso do volume em decibéis, usado só pelo protocolo de texto.",
        "campo_db_maximo": "O teto do volume em decibéis, usado só pelo protocolo de texto.",
        "cap_volume": (
            "Convertido dos decibéis do protocolo de texto ou do zero a duzentos do binário."
        ),
    },
}

SUGESTOES = (
    Sugestao("entradas", "Coax 1", "Coaxial 1"),
    Sugestao("entradas", "Optica 1", "Optical 1"),
    Sugestao("entradas", "Bluetooth", "Bluetooth"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the amplifier."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Nad(Driver):
    """One NAD amplifier, speaking whichever of its two protocols the registration says."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Amplificador NAD", "en": "NAD amplifier"},
        categoria="amplificador",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_VOLUME, ACAO_MUDO, ACAO_FONTE),
        config_campos=(
            Campo(nome=CAMPO_PROTOCOLO, tipo=TipoCampo.TEXTO, padrao=PROTOCOLO_TEXTO),
            Campo(nome=CAMPO_DB_MINIMO, tipo=TipoCampo.TEXTO, padrao=str(DB_MINIMO_PADRAO)),
            Campo(nome=CAMPO_DB_MAXIMO, tipo=TipoCampo.TEXTO, padrao=str(DB_MAXIMO_PADRAO)),
        ),
        # The amplifier announces nothing this hub can claim, so it is found by the range
        # sweep, which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="NAD",
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
            if self._protocolo() == PROTOCOLO_BINARIO:
                await self._ler_estado_binario()
            else:
                await self._ler_estado_de_texto()
        except _Falha as falha:
            await self._descartar()
            self._falhar(falha.codigo)
            return
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            if self._protocolo() == PROTOCOLO_BINARIO:
                return await self._agir_binario(acao, valor)
            return await self._agir_de_texto(acao, valor)
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                await self._descartar()
            return falha.codigo

    # ---------------------------------------------------------------- text
    async def _agir_de_texto(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            await self._escrever_texto(f"{TEXTO_ENERGIA}={LIGADO}")
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._escrever_texto(f"{TEXTO_ENERGIA}={DESLIGADO}")
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._escrever_texto(f"{TEXTO_VOLUME}={self._para_db(valor)}")
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._escrever_texto(f"{TEXTO_MUDO}={LIGADO if valor else DESLIGADO}")
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            entrada = _palavra(valor)
            if entrada is None:
                return INVALID_VALUE
            await self._escrever_texto(f"{TEXTO_FONTE}={entrada}")
            self._defina(fonte=entrada)
            return None
        return NAO_SUPORTADO

    async def _ler_estado_de_texto(self) -> None:
        lidas = await self._perguntar_de_texto(
            (TEXTO_ENERGIA, TEXTO_VOLUME, TEXTO_MUDO, TEXTO_FONTE)
        )
        energia = lidas.get(TEXTO_ENERGIA, "")
        mudo = lidas.get(TEXTO_MUDO, "")
        self._defina(
            online=True,
            ligado=None if not energia else energia == LIGADO,
            volume=self._do_db(lidas.get(TEXTO_VOLUME, "")),
            mudo=None if not mudo else mudo == LIGADO,
            fonte=lidas.get(TEXTO_FONTE) or None,
            detalhe="",
        )

    async def _perguntar_de_texto(self, nomes: tuple[str, ...]) -> dict[str, str]:
        """Every question in one write, and the answers read by the name each one repeats."""
        leitor, escritor = await self._conectar()
        pedido = "".join(f"{nome}?\r" for nome in nomes)
        self._fio.enviado(" ".join(nomes), rotina=True)
        try:
            escritor.write(pedido.encode("ascii"))
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(pedido, erro)
            raise _Falha(EQ_OFFLINE) from erro
        lidas: dict[str, str] = {}
        for _ in range(LINHAS_ATE_DESISTIR):
            if all(nome in lidas for nome in nomes):
                return lidas
            try:
                async with asyncio.timeout(RESPOSTA_S):
                    bruto = await leitor.readuntil(FIM_DA_LINHA)
            except TimeoutError:
                break
            except (OSError, asyncio.IncompleteReadError, ValueError) as erro:
                self._fio.falhou("read", erro)
                raise _Falha(EQ_OFFLINE) from erro
            linha = bruto.decode("ascii", errors="replace").strip()
            self._fio.recebido(linha, rotina=True)
            casado = _RESPOSTA_DE_TEXTO.match(linha)
            if casado is not None:
                lidas[casado.group("nome")] = casado.group("valor").strip()
        if TEXTO_ENERGIA not in lidas:
            raise _Falha(ERRO_APARELHO)
        return lidas

    async def _escrever_texto(self, sentenca: str) -> None:
        _leitor, escritor = await self._conectar()
        self._fio.enviado(sentenca)
        try:
            escritor.write(sentenca.encode("ascii") + FIM_DA_LINHA)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(sentenca, erro)
            raise _Falha(EQ_OFFLINE) from erro

    # ---------------------------------------------------------------- binary
    async def _agir_binario(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            await self._escrever_bytes(BIN_LIGAR)
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            # Turning it off without the power saving message in front hangs the amplifier.
            await self._escrever_bytes(BIN_ECONOMIA + BIN_DESLIGAR)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            nivel = round(valor * BIN_VOLUME_MAXIMO / 100)
            await self._escrever_bytes(BIN_VOLUME + bytes([nivel & 0xFF]))
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._escrever_bytes(BIN_MUDO if valor else BIN_SEM_MUDO)
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            codigo = _codigo_da_fonte(valor)
            if codigo is None:
                return INVALID_VALUE
            await self._escrever_bytes(BIN_FONTE + bytes.fromhex(codigo))
            self._defina(fonte=BIN_FONTES[codigo])
            return None
        return NAO_SUPORTADO

    async def _ler_estado_binario(self) -> None:
        """One poll: the four questions in one write, and four answers of ten bytes back."""
        perguntas = (
            BIN_PERGUNTA_VOLUME + BIN_PERGUNTA_ENERGIA + BIN_PERGUNTA_MUDO + BIN_PERGUNTA_FONTE
        )
        respostas = await self._trocar_bytes(perguntas, BIN_RESPOSTA * 4)
        pedacos = [respostas[i : i + BIN_RESPOSTA] for i in range(0, len(respostas), BIN_RESPOSTA)]
        if len(pedacos) < 4:
            raise _Falha(ERRO_APARELHO)
        codigo = pedacos[3][-1:].hex()
        self._defina(
            online=True,
            volume=max(0, min(100, round(pedacos[0][-1] * 100 / BIN_VOLUME_MAXIMO))),
            ligado=pedacos[1][-1] == 1,
            mudo=pedacos[2][-1] == 1,
            fonte=BIN_FONTES.get(codigo),
            fontes=tuple(BIN_FONTES.values()),
            detalhe="",
        )

    async def _escrever_bytes(self, mensagem: bytes) -> None:
        _leitor, escritor = await self._conectar()
        self._fio.enviado(mensagem.hex())
        try:
            escritor.write(mensagem)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(mensagem.hex(), erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _trocar_bytes(self, mensagem: bytes, quantos: int) -> bytes:
        leitor, escritor = await self._conectar()
        self._fio.enviado(mensagem.hex(), rotina=True)
        try:
            escritor.write(mensagem)
            await escritor.drain()
            async with asyncio.timeout(RESPOSTA_S):
                lido = await leitor.readexactly(quantos)
        except TimeoutError:
            self._fio.falhou(mensagem.hex(), "no answer")
            raise _Falha(ERRO_APARELHO) from None
        except (OSError, asyncio.IncompleteReadError, RuntimeError, ValueError) as erro:
            self._fio.falhou(mensagem.hex(), erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(lido.hex(), rotina=True)
        return lido

    # ---------------------------------------------------------------- both
    async def _conectar(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        leitor, escritor = self._leitor, self._escritor
        if leitor is not None and escritor is not None and not escritor.is_closing():
            return leitor, escritor
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        porta = PORTA_BINARIA if self._protocolo() == PROTOCOLO_BINARIO else PORTA_TEXTO
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(endereco, porta)
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

    def _protocolo(self) -> str:
        bruto = self.cadastro.campos.get(CAMPO_PROTOCOLO, "").strip().lower()
        return bruto if bruto in PROTOCOLOS else PROTOCOLO_TEXTO

    def _faixa(self) -> tuple[float, float]:
        minimo = _decimal(self.cadastro.campos.get(CAMPO_DB_MINIMO, ""), DB_MINIMO_PADRAO)
        maximo = _decimal(self.cadastro.campos.get(CAMPO_DB_MAXIMO, ""), DB_MAXIMO_PADRAO)
        return (minimo, maximo) if minimo < maximo else (DB_MINIMO_PADRAO, DB_MAXIMO_PADRAO)

    def _do_db(self, bruto: str) -> int | None:
        if not _NUMERO.match(bruto.strip()):
            return None
        minimo, maximo = self._faixa()
        db = max(minimo, min(maximo, float(bruto)))
        return round((db - minimo) * 100 / (maximo - minimo))

    def _para_db(self, valor: int) -> str:
        minimo, maximo = self._faixa()
        db = minimo + valor * (maximo - minimo) / 100
        # The amplifier takes whole and half decibels and nothing between them.
        return f"{round(db * 2) / 2:g}"

    def _id(self) -> str:
        return self.cadastro.identidade


def _palavra(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()
    return limpo if _PALAVRA.match(limpo) else None


def _codigo_da_fonte(valor: object) -> str | None:
    """The code of the input, from the name of the fixed table of this line."""
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()
    for codigo, nome in BIN_FONTES.items():
        if nome.casefold() == limpo.casefold():
            return codigo
    return None


def _decimal(bruto: str, padrao: float) -> float:
    try:
        return float(bruto.strip())
    except ValueError:
        return padrao
