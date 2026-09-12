# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Pioneer receivers over the control port of the receiver itself.

What the protocol is, so nobody has to read it again:

- One TCP connection, one command per line, ended by a carriage return, answered by a line
  ended by carriage return and line feed. The port is 8102 on the models that carry the
  dedicated control port and 23 on the ones that only carry telnet, and nothing in a
  registration tells them apart, so the driver tries both and keeps the one that answered.
- A question is a command with a question mark: ?P for power, ?V for volume, ?M for mute,
  ?F for the input in front. The answer of each carries its own prefix (PWR, VOL, MUT, FN),
  which matters because the receiver ALSO pushes a line of its own whenever somebody touches
  the front panel, so an answer is matched by prefix and anything else is dropped.
- Power is PWR0 for on and PWR1 or PWR2 for off; PO turns it on and PF turns it off. Mute is
  MUT0 for muted, which reads backwards and is what the protocol says.
- The volume is a whole number from zero to a ceiling of the model, 185 on almost every one
  of them and 161 on a few, which is a field of the registration because the receiver does
  not answer what its own ceiling is.
- The inputs are asked in one burst: ?RGB00 to ?RGB59 answers RGB<number><flag><name> for
  every input the model HAS and NOTHING for the numbers it does not, so the answers are read
  until the receiver goes quiet and the list of inputs is read from the receiver, never
  guessed from a table. What travels as the input is the name the receiver gave it.
"""

import asyncio
import logging
import re

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import (
    Campo,
    Descoberta,
    Manifesto,
    Sugestao,
    TipoCampo,
)

log = logging.getLogger("iphub.drivers.nativos.pioneer")

TIPO = "receiver_pioneer"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

# The control port of the models that have one, and the telnet port of the ones that do not.
PORTAS = (8102, 23)
CONEXAO_S = 3.0
RESPOSTA_S = 2.0
LINHAS_ATE_DESISTIR = 6
# Reading the inputs is one burst of questions and one burst of answers: the silence says the
# receiver finished, and the two ceilings keep a receiver that never stops from holding a poll.
SILENCIO_S = 0.6
LEITURA_TOTAL_S = 8.0
LINHAS_MAXIMAS = 200
LINHA_MAXIMA = 512
FALHAS_ATE_OFFLINE = 2

TERMINADOR = b"\r"
FIM_DE_LINHA = b"\r\n"

CAMPO_VOLUME_MAXIMO = "volume_maximo"
VOLUME_MAXIMO_PADRAO = 185
VOLUME_MAXIMO_MINIMO = 1
VOLUME_MAXIMO_MAXIMO = 255

ENTRADAS_ATE = 60

PERGUNTA_ENERGIA = "?P"
PERGUNTA_VOLUME = "?V"
PERGUNTA_MUDO = "?M"
PERGUNTA_FONTE = "?F"
PERGUNTA_NOME_DA_ENTRADA = "?RGB"

PREFIXO_ENERGIA = "PWR"
PREFIXO_VOLUME = "VOL"
PREFIXO_MUDO = "MUT"
PREFIXO_FONTE = "FN"
PREFIXO_NOME_DA_ENTRADA = "RGB"

LIGAR = "PO"
DESLIGAR = "PF"
MUDO_LIGADO = "MO"
MUDO_DESLIGADO = "MF"

LIGADO = "0"
MUDO = "0"

_NOME_DE_ENTRADA = re.compile(r"^RGB(?P<numero>[0-9]{2}).(?P<nome>.*)$")
_SO_DIGITOS = re.compile(r"^[0-9]+$")
# A receiver that answers an error answers E followed by a digit, and that is not a state.
_ERRO = re.compile(r"^E[0-9]+$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TECLA = "tecla"

TECLAS = {"mais": "VU", "menos": "VD"}

TEXTOS = {
    "en": {
        "descricao": (
            "Pioneer receiver over the control port of the receiver, with no cloud and no "
            "account. The inputs are read from the receiver itself, so the list is the list "
            "of that model."
        ),
        "preparo": (
            "On the receiver: Network Setup, and leave Network Standby on, or the receiver "
            "stops answering the network when it is turned off and only the remote turns it "
            "on again."
        ),
        "campo_volume_maximo": (
            "The volume ceiling of the model, 185 on almost every one and 161 on a few. The "
            "receiver does not answer what its own is, and a wrong one makes the level of "
            "the panel land above or below the level of the receiver."
        ),
        "cap_volume": (
            "Converted from the scale of the receiver, which counts to the ceiling above."
        ),
    },
    "pt": {
        "descricao": (
            "Receiver Pioneer pela porta de controle do próprio receiver, sem nuvem e sem "
            "conta. As entradas são lidas do próprio receiver, então a lista é a lista "
            "daquele modelo."
        ),
        "preparo": (
            "No receiver: Network Setup, e deixe o Network Standby ligado, senão o receiver "
            "para de responder à rede quando é desligado e só o controle o liga de novo."
        ),
        "campo_volume_maximo": (
            "O teto de volume do modelo, 185 em quase todos e 161 em alguns. O receiver não "
            "responde qual é o dele, e um teto errado faz o nível do painel cair acima ou "
            "abaixo do nível do receiver."
        ),
        "cap_volume": "Convertido da escala do receiver, que conta até o teto acima.",
    },
}

# What the receiver calls its own inputs is read from it, so these are only the ones almost
# every model carries, for a registration made before the first poll.
SUGESTOES = (
    Sugestao("entradas", "BD", "BD"),
    Sugestao("entradas", "TV", "TV"),
    Sugestao("entradas", "NETWORK", "NETWORK"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the receiver."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Pioneer(Driver):
    """One Pioneer receiver, asked and commanded one line at a time."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Pioneer", "en": "Pioneer receiver"},
        categoria="receiver",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TECLA,
        ),
        teclas=tuple(TECLAS),
        config_campos=(
            Campo(
                nome=CAMPO_VOLUME_MAXIMO,
                tipo=TipoCampo.INTEIRO,
                padrao=str(VOLUME_MAXIMO_PADRAO),
            ),
        ),
        # The receiver announces nothing this hub can claim, so it is found by the range
        # sweep, which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Pioneer",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._leitor: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._porta_que_respondeu: int | None = None
        self._entradas: dict[str, str] = {}
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
            await self._mandar(LIGAR)
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._mandar(DESLIGAR)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._mandar(f"{self._para_o_aparelho(valor):03d}VL")
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._mandar(MUDO_LIGADO if valor else MUDO_DESLIGADO)
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            numero = self._numero_da_fonte(valor)
            if numero is None:
                return INVALID_VALUE
            await self._mandar(f"{numero}FN")
            self._defina(fonte=self._entradas.get(numero, numero))
            return None
        if acao == ACAO_TECLA:
            comando = TECLAS.get(valor) if isinstance(valor, str) else None
            if comando is None:
                return INVALID_VALUE
            await self._mandar(comando)
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: power, volume, mute and the input in front, plus the inputs once."""
        energia = await self._perguntar(PERGUNTA_ENERGIA, PREFIXO_ENERGIA, rotina=True)
        ligado = energia == LIGADO
        volume = await self._perguntar(PERGUNTA_VOLUME, PREFIXO_VOLUME, rotina=True)
        mudo = await self._perguntar(PERGUNTA_MUDO, PREFIXO_MUDO, rotina=True)
        fonte = await self._perguntar(PERGUNTA_FONTE, PREFIXO_FONTE, rotina=True)
        if not self._entradas:
            await self._ler_entradas()
        self._defina(
            online=True,
            ligado=ligado,
            volume=self._do_aparelho(volume),
            mudo=mudo == MUDO if mudo else None,
            # The receiver answers a number and names its own inputs, so what travels is the
            # name it gave: a scene that says BD reads as BD and not as 25.
            fonte=self._entradas.get(fonte, fonte) or None,
            fontes=tuple(self._entradas.values()),
            detalhe="",
        )

    async def _ler_entradas(self) -> None:
        """Which inputs this model HAS and what it calls them, read from the receiver once.

        The sixty questions go out together and the answers are read until the receiver goes
        quiet, because it answers NOTHING for a number its model does not carry: asked one at
        a time, the sixty silences would cost sixty timeouts and the poll would never end.
        """
        _leitor, escritor = await self._conectar()
        perguntas = b"".join(
            f"{PERGUNTA_NOME_DA_ENTRADA}{numero:02d}".encode("ascii") + TERMINADOR
            for numero in range(ENTRADAS_ATE)
        )
        self._fio.enviado(f"{PERGUNTA_NOME_DA_ENTRADA}00..{ENTRADAS_ATE - 1:02d}", rotina=True)
        try:
            escritor.write(perguntas)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(PERGUNTA_NOME_DA_ENTRADA, erro)
            raise _Falha(EQ_OFFLINE) from erro
        achadas: dict[str, str] = {}
        for linha in await self._ler_ate_calar():
            casado = _NOME_DE_ENTRADA.match(linha)
            if casado is not None:
                nome = casado.group("nome").strip()
                achadas[casado.group("numero")] = nome or casado.group("numero")
        self._entradas = achadas
        self._fio.recebido(f"{len(achadas)} inputs", rotina=True)
        log.info("%s: the receiver has inputs %s", self._id(), sorted(achadas.items()))

    async def _ler_ate_calar(self) -> list[str]:
        """Every line the receiver sends until it stops sending, within one deadline."""
        leitor, _escritor = await self._conectar()
        linhas: list[str] = []
        try:
            async with asyncio.timeout(LEITURA_TOTAL_S):
                while len(linhas) < LINHAS_MAXIMAS:
                    try:
                        async with asyncio.timeout(SILENCIO_S):
                            bruto = await leitor.readuntil(FIM_DE_LINHA)
                    except TimeoutError:
                        return linhas
                    linhas.append(bruto.decode("ascii", errors="replace").strip())
        except TimeoutError:
            return linhas
        except (OSError, asyncio.IncompleteReadError, ValueError) as erro:
            self._fio.falhou("read", erro)
            raise _Falha(EQ_OFFLINE) from erro
        return linhas

    async def _mandar(self, comando: str) -> None:
        """A command the receiver does not answer with a value, only with its new state."""
        _leitor, escritor = await self._conectar()
        self._fio.enviado(comando)
        try:
            escritor.write(comando.encode("ascii") + TERMINADOR)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(comando, erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _perguntar(self, comando: str, prefixo: str, *, rotina: bool = False) -> str:
        """The answer of THAT question, with the lines the receiver pushes on its own dropped.

        Returns what comes after the prefix, or empty when the receiver said nothing about it,
        which is how a model without that input answers the question about it.
        """
        leitor, escritor = await self._conectar()
        self._fio.enviado(comando, rotina=rotina)
        try:
            escritor.write(comando.encode("ascii") + TERMINADOR)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(comando, erro)
            raise _Falha(EQ_OFFLINE) from erro
        for _ in range(LINHAS_ATE_DESISTIR):
            try:
                async with asyncio.timeout(RESPOSTA_S):
                    bruto = await leitor.readuntil(FIM_DE_LINHA)
            except TimeoutError:
                self._fio.falhou(comando, "no answer")
                raise _Falha(ERRO_APARELHO) from None
            except (OSError, asyncio.IncompleteReadError, ValueError) as erro:
                self._fio.falhou(comando, erro)
                raise _Falha(EQ_OFFLINE) from erro
            linha = bruto.decode("ascii", errors="replace").strip()
            self._fio.recebido(linha, rotina=rotina)
            if _ERRO.match(linha):
                raise _Falha(ERRO_APARELHO)
            if linha.startswith(prefixo):
                return linha[len(prefixo) :]
            log.debug("%s: line of the receiver dropped: %s", self._id(), linha)
        raise _Falha(ERRO_APARELHO)

    async def _conectar(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """The open connection, or a new one on the port of this model.

        The port that answered is kept, so the model that only has telnet costs one refused
        connection at the first poll and none after it.
        """
        leitor, escritor = self._leitor, self._escritor
        if leitor is not None and escritor is not None and not escritor.is_closing():
            return leitor, escritor
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        portas = (self._porta_que_respondeu,) if self._porta_que_respondeu is not None else PORTAS
        ultimo: Exception | None = None
        for porta in portas:
            try:
                async with asyncio.timeout(CONEXAO_S):
                    leitor, escritor = await asyncio.open_connection(
                        endereco, porta, limit=LINHA_MAXIMA
                    )
            except (OSError, TimeoutError) as erro:
                ultimo = erro
                continue
            self._leitor, self._escritor = leitor, escritor
            self._porta_que_respondeu = porta
            self._fio.reconectou()
            return leitor, escritor
        self._fio.falhou("connect", ultimo or "no port answered")
        raise _Falha(EQ_OFFLINE)

    async def _descartar(self) -> None:
        escritor = self._escritor
        self._leitor = self._escritor = None
        self._entradas = {}
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

    def _teto(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_VOLUME_MAXIMO, "")
        if not _SO_DIGITOS.match(bruto.strip()):
            return VOLUME_MAXIMO_PADRAO
        teto = int(bruto)
        if not VOLUME_MAXIMO_MINIMO <= teto <= VOLUME_MAXIMO_MAXIMO:
            return VOLUME_MAXIMO_PADRAO
        return teto

    def _do_aparelho(self, bruto: str) -> int | None:
        if not _SO_DIGITOS.match(bruto):
            return None
        return max(0, min(100, round(int(bruto) * 100 / self._teto())))

    def _para_o_aparelho(self, valor: int) -> int:
        return max(0, min(self._teto(), round(valor * self._teto() / 100)))

    def _numero_da_fonte(self, valor: object) -> str | None:
        """The number of the input, from the name the receiver gave it or from the number.

        The name is what a scene and the app carry, and the number is what a registration
        made before the receiver named anything carries, so both are accepted.
        """
        if not isinstance(valor, str):
            return None
        limpo = valor.strip()
        for numero, nome in self._entradas.items():
            if nome.casefold() == limpo.casefold():
                return numero
        return _numero_de_entrada(limpo)

    def _id(self) -> str:
        return self.cadastro.identidade


def _numero_de_entrada(valor: object) -> str | None:
    """An input of this receiver is a number of two digits, and nothing else is."""
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()
    if not _SO_DIGITOS.match(limpo) or len(limpo) > 2:
        return None
    return f"{int(limpo):02d}"
