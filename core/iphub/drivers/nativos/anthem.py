# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Anthem receivers and processors over the IP control of the unit itself.

What the protocol is, so nobody has to read it again:

- One TCP connection on port 14999, and the semicolon is the terminator: `Z1POW1;` turns
  zone one on and `Z1POW?;` asks about it. The unit answers with datagrams of the same
  shape, several of them in one write when a lot happened at once.
- A question is the command with a question mark, and the answer repeats the command with
  the value glued to it, so an answer is matched by the command in front of it and anything
  else the unit pushes is kept for what it is: a state that changed.
- The volume has TWO forms. The current models answer `Z1PVOL`, which is already a
  percentage, and the older ones only answer `Z1VOL`, which is an attenuation in decibels
  from a floor of -90. The driver asks for both, uses the percentage when the unit has one,
  and converts the decibels when it does not.
- The inputs are numbered, `Z1INP3`, and named apart: `ICN?` answers how many the unit has
  and `ISN01` to `ISN12` answer the name of each, so the list of inputs is read from the
  unit and never guessed.
- A zone of the unit is a registration of its own, which is what the Z of every command is.
"""

import asyncio
import logging
import re

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.anthem")

TIPO = "receiver_anthem"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA = 14999
CONEXAO_S = 3.0
RESPOSTA_S = 2.0
DATAGRAMAS_ATE_DESISTIR = 24
LEITURA_MAXIMA = 4096
FALHAS_ATE_OFFLINE = 2

FIM = ";"
PERGUNTA = "?"

CAMPO_ZONA = "zona"
ZONA_PADRAO = 1
ZONA_MAXIMA = 4

# The attenuation of the models that do not answer a percentage, in decibels.
DB_MINIMO = -90.0
DB_MAXIMO = 0.0

ENTRADAS_MAXIMAS = 12

CMD_ENERGIA = "POW"
CMD_VOLUME_PORCENTO = "PVOL"
CMD_VOLUME_DB = "VOL"
CMD_MUDO = "MUT"
CMD_ENTRADA = "INP"
CMD_QUANTAS_ENTRADAS = "ICN"
CMD_NOME_DA_ENTRADA = "ISN"
CMD_MODELO = "IDM"
CMD_MAC = "IDN"

_NOME_DA_ENTRADA = re.compile(r"^ISN(?P<numero>[0-9]{2})(?P<nome>.+)$")
_INTEIRO = re.compile(r"^-?[0-9]+$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"

TEXTOS = {
    "en": {
        "descricao": (
            "Anthem receiver or processor over the IP control of the unit, with no cloud and "
            "no account. The inputs are read from the unit, so the list is the list of that "
            "model. One registration is ONE ZONE."
        ),
        "preparo": (
            "On the unit: Setup, Network, and leave Standby IP Control on, or the unit stops "
            "answering the network when it is off and only the remote turns it on again."
        ),
        "campo_zona": "The zone of the unit, 1 to 4. One zone is one registration.",
        "cap_volume": (
            "The percentage the current models answer, or the attenuation in decibels of the "
            "older ones converted from a floor of -90 dB."
        ),
    },
    "pt": {
        "descricao": (
            "Receiver ou processador Anthem pelo controle IP da própria unidade, sem nuvem e "
            "sem conta. As entradas são lidas da unidade, então a lista é a lista daquele "
            "modelo. Um cadastro é UMA ZONA."
        ),
        "preparo": (
            "Na unidade: Setup, Network, e deixe o Standby IP Control ligado, senão a unidade "
            "para de responder à rede quando está desligada e só o controle a liga de novo."
        ),
        "campo_zona": "A zona da unidade, 1 a 4. Uma zona é um cadastro.",
        "cap_volume": (
            "A porcentagem que os modelos atuais respondem, ou a atenuação em decibéis dos "
            "mais antigos convertida de um piso de -90 dB."
        ),
    },
}

SUGESTOES = (
    Sugestao("entradas", "Entrada 1", "1"),
    Sugestao("entradas", "Entrada 2", "2"),
    Sugestao("entradas", "Entrada 3", "3"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the unit."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Anthem(Driver):
    """One zone of an Anthem unit, asked and commanded one semicolon at a time."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Anthem", "en": "Anthem receiver"},
        categoria="receiver",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_VOLUME, ACAO_MUDO, ACAO_FONTE),
        config_campos=(Campo(nome=CAMPO_ZONA, tipo=TipoCampo.INTEIRO, padrao=str(ZONA_PADRAO)),),
        # The unit announces nothing this hub can claim, so it is found by the range sweep,
        # which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Anthem",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._leitor: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._entradas: dict[str, str] = {}
        self._tem_porcento: bool | None = None
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
            await self._mandar(f"{self._z(CMD_ENERGIA)}1")
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._mandar(f"{self._z(CMD_ENERGIA)}0")
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            if self._tem_porcento:
                await self._mandar(f"{self._z(CMD_VOLUME_PORCENTO)}{valor}")
            else:
                await self._mandar(f"{self._z(CMD_VOLUME_DB)}{_para_db(valor)}")
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._mandar(f"{self._z(CMD_MUDO)}{1 if valor else 0}")
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            numero = self._numero_da_entrada(valor)
            if numero is None:
                return INVALID_VALUE
            await self._mandar(f"{self._z(CMD_ENTRADA)}{numero}")
            self._defina(fonte=self._entradas.get(numero, numero))
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: the questions of the zone in one write, the answers read by command."""
        perguntas = [
            self._z(CMD_ENERGIA),
            self._z(CMD_VOLUME_PORCENTO),
            self._z(CMD_VOLUME_DB),
            self._z(CMD_MUDO),
            self._z(CMD_ENTRADA),
        ]
        if not self._entradas:
            perguntas.append(CMD_QUANTAS_ENTRADAS)
            perguntas += [
                f"{CMD_NOME_DA_ENTRADA}{numero:02d}" for numero in range(1, ENTRADAS_MAXIMAS + 1)
            ]
        lidas = await self._perguntar(tuple(perguntas), self._z(CMD_ENERGIA))
        self._guardar_entradas(lidas)
        porcento = lidas.get(self._z(CMD_VOLUME_PORCENTO), "")
        db = lidas.get(self._z(CMD_VOLUME_DB), "")
        if self._tem_porcento is None:
            self._tem_porcento = bool(porcento)
        energia = lidas.get(self._z(CMD_ENERGIA), "")
        entrada = lidas.get(self._z(CMD_ENTRADA), "")
        self._defina(
            online=True,
            ligado=None if not energia else energia == "1",
            volume=_do_porcento(porcento) if porcento else _do_db(db),
            mudo=None if not lidas.get(self._z(CMD_MUDO)) else lidas[self._z(CMD_MUDO)] == "1",
            fonte=self._entradas.get(entrada, entrada) or None,
            fontes=tuple(self._entradas.values()),
            detalhe="",
        )

    def _guardar_entradas(self, lidas: dict[str, str]) -> None:
        """The names the unit gave its own inputs, as they arrive in the answers.

        A unit answers nothing about an input it does not have, so what came back IS the list.
        """
        achadas: dict[str, str] = {}
        for comando, valor in lidas.items():
            casado = _NOME_DA_ENTRADA.match(f"{comando}{valor}")
            if casado is not None and casado.group("nome").strip():
                achadas[str(int(casado.group("numero")))] = casado.group("nome").strip()
        if achadas:
            self._entradas = achadas
            log.info("%s: the unit has inputs %s", self._id(), sorted(achadas.items()))

    async def _mandar(self, comando: str) -> None:
        _leitor, escritor = await self._conectar()
        self._fio.enviado(comando)
        try:
            escritor.write(f"{comando}{FIM}".encode("ascii"))
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(comando, erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _perguntar(self, comandos: tuple[str, ...], obrigatorio: str) -> dict[str, str]:
        """Every question in one write, and the answers read by the command in front of them.

        A unit that does not have one of the commands answers nothing about it, so what is
        waited for is the one answer no model of this protocol is without.
        """
        leitor, escritor = await self._conectar()
        pedido = "".join(f"{comando}{PERGUNTA}{FIM}" for comando in comandos)
        self._fio.enviado(" ".join(comandos), rotina=True)
        try:
            escritor.write(pedido.encode("ascii"))
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(pedido, erro)
            raise _Falha(EQ_OFFLINE) from erro
        lidas: dict[str, str] = {}
        buffer = ""
        for _ in range(DATAGRAMAS_ATE_DESISTIR):
            if obrigatorio in lidas and len(lidas) >= len(comandos):
                break
            try:
                async with asyncio.timeout(RESPOSTA_S):
                    pedaco = await leitor.read(LEITURA_MAXIMA)
            except TimeoutError:
                break
            except (OSError, ValueError) as erro:
                self._fio.falhou("read", erro)
                raise _Falha(EQ_OFFLINE) from erro
            if not pedaco:
                raise _Falha(EQ_OFFLINE)
            buffer += pedaco.decode("ascii", errors="replace")
            while FIM in buffer:
                bruto, buffer = buffer.split(FIM, 1)
                self._fio.recebido(bruto, rotina=True)
                _guardar(lidas, bruto.strip(), comandos)
        if obrigatorio not in lidas:
            raise _Falha(ERRO_APARELHO)
        return lidas

    async def _conectar(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        leitor, escritor = self._leitor, self._escritor
        if leitor is not None and escritor is not None and not escritor.is_closing():
            return leitor, escritor
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(endereco, PORTA)
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

    def _z(self, comando: str) -> str:
        return f"Z{self._zona()}{comando}"

    def _zona(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_ZONA, "").strip()
        if not bruto.isdigit():
            return ZONA_PADRAO
        zona = int(bruto)
        return zona if 1 <= zona <= ZONA_MAXIMA else ZONA_PADRAO

    def _numero_da_entrada(self, valor: object) -> str | None:
        """The number of the input, from the name the unit gave it or from the number."""
        if not isinstance(valor, str):
            return None
        limpo = valor.strip()
        for numero, nome in self._entradas.items():
            if nome.casefold() == limpo.casefold():
                return numero
        if limpo.isdigit() and 1 <= int(limpo) <= ENTRADAS_MAXIMAS:
            return str(int(limpo))
        return None

    def _id(self) -> str:
        return self.cadastro.identidade

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The MAC of the unit, which it answers with no pairing and keeps for good."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(endereco, PORTA)
        except (OSError, TimeoutError):
            return None
        try:
            escritor.write(f"{CMD_MAC}{PERGUNTA}{FIM}".encode("ascii"))
            await escritor.drain()
            async with asyncio.timeout(RESPOSTA_S):
                bruto = await leitor.read(LEITURA_MAXIMA)
        except (OSError, TimeoutError, RuntimeError):
            return None
        finally:
            escritor.close()
            try:
                await escritor.wait_closed()
            except (OSError, RuntimeError, TimeoutError, asyncio.CancelledError):
                log.debug("%s: the socket did not close cleanly", ip)
        lidas: dict[str, str] = {}
        for datagrama in bruto.decode("ascii", errors="replace").split(FIM):
            _guardar(lidas, datagrama.strip(), (CMD_MAC,))
        return lidas.get(CMD_MAC, "").lower() or None


def _guardar(lidas: dict[str, str], bruto: str, comandos: tuple[str, ...]) -> None:
    """One datagram filed under the command it answers, or dropped when it answers none.

    The command and the value are glued together with no separator, so the only way to tell
    them apart is to know which command was asked: the longest one that fits the front of
    the datagram is the one that answered, and the rest of it is the value. A datagram of a
    command nobody asked is the unit reporting a change, and there is nothing to file it as.
    """
    for comando in sorted(comandos, key=len, reverse=True):
        if bruto.startswith(comando):
            lidas[comando] = bruto[len(comando) :].strip()
            return


def _do_porcento(bruto: str) -> int | None:
    if not _INTEIRO.match(bruto):
        return None
    return max(0, min(100, int(bruto)))


def _do_db(bruto: str) -> int | None:
    if not _INTEIRO.match(bruto):
        return None
    db = max(DB_MINIMO, min(DB_MAXIMO, float(bruto)))
    return round((db - DB_MINIMO) * 100 / (DB_MAXIMO - DB_MINIMO))


def _para_db(valor: int) -> int:
    return round(DB_MINIMO + valor * (DB_MAXIMO - DB_MINIMO) / 100)
