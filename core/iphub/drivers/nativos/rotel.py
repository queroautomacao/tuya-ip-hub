# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Rotel amplifiers and preamplifiers over the ASCII control of the unit itself.

What the protocol is, so nobody has to read it again:

- One TCP connection on port 9590, and the command IS the terminator: every command ends in
  an exclamation mark and carries no carriage return and no space. `power_on!` is the whole
  frame.
- The answer ends in a dollar sign on the current protocol and in an exclamation mark on the
  units still running the older one, so both are read as the end of an answer.
- An answer is a name and a value, `power=on$`, `volume=40$`, `source=coax1$`, so it is
  matched by the name in front of the equals sign. The unit also pushes those same lines on
  its own whenever the front panel moves, which is why nothing is read positionally.
- The volume is a whole number from one to a ceiling of the model, 96 on the current ones and
  86 on the ones made before that correction, which is a field of the registration because
  the unit does not answer what its own ceiling is.
- The inputs are words of the model (`cd`, `coax1`, `opt1`, `tuner`, `phono`, `usb`,
  `bluetooth`, `bal_xlr`, `pcusb`, `aux`), and the unit answers the one in front by name.
"""

import asyncio
import logging
import re

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.rotel")

TIPO = "receiver_rotel"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA = 9590
CONEXAO_S = 3.0
RESPOSTA_S = 2.0
RESPOSTAS_ATE_DESISTIR = 8
LEITURA_MAXIMA = 4096
FALHAS_ATE_OFFLINE = 2

FIM_DO_COMANDO = b"!"
FINS_DA_RESPOSTA = ("$", "!")

CAMPO_VOLUME_MAXIMO = "volume_maximo"
VOLUME_MAXIMO_PADRAO = 96
VOLUME_MAXIMO_MINIMO = 2
VOLUME_MAXIMO_MAXIMO = 200

LIGADO = "on"
STANDBY = "standby"

# What the unit calls each thing it answers, which is the word in front of the equals sign.
NOME_ENERGIA = "power"
NOME_VOLUME = "volume"
NOME_MUDO = "mute"
NOME_FONTE = "source"
NOME_MAC = "mac"

_RESPOSTA = re.compile(r"^(?P<nome>[a-z_0-9]+)=(?P<valor>.*)$")
_SO_DIGITOS = re.compile(r"^[0-9]+$")
# An input of this unit is a lowercase word with digits and underscores, and nothing else is.
_PALAVRA = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TECLA = "tecla"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"

TECLAS = {"mais": "vol_up", "menos": "vol_dwn"}
TRANSPORTE = {
    ACAO_TOCAR: "play",
    ACAO_PAUSAR: "pause",
    ACAO_PARAR: "stop",
    ACAO_PROXIMA: "trkf",
    ACAO_ANTERIOR: "trkb",
}

TEXTOS = {
    "en": {
        "descricao": (
            "Rotel amplifier or preamplifier over the ASCII control of the unit, with no "
            "cloud and no account: power, volume, mute, input and the transport of the "
            "source in front."
        ),
        "preparo": (
            "On the unit: Setup, Network, and leave the network on in standby, or the unit "
            "stops answering when it is off. The IP control needs nothing else."
        ),
        "campo_volume_maximo": (
            "The volume ceiling of the model, 96 on the current ones and 86 on the ones made "
            "before that correction. The unit does not answer what its own is, and a wrong "
            "one makes the level of the panel land above or below the level of the unit."
        ),
        "cap_volume": "Converted from the scale of the unit, which counts to the ceiling above.",
    },
    "pt": {
        "descricao": (
            "Amplificador ou pré-amplificador Rotel pelo controle ASCII da própria unidade, "
            "sem nuvem e sem conta: energia, volume, mudo, entrada e o transporte da fonte "
            "em frente."
        ),
        "preparo": (
            "Na unidade: Setup, Network, e deixe a rede ligada em standby, senão a unidade "
            "para de responder quando está desligada. O controle por IP não precisa de mais "
            "nada."
        ),
        "campo_volume_maximo": (
            "O teto de volume do modelo, 96 nos atuais e 86 nos feitos antes daquela "
            "correção. A unidade não responde qual é o dela, e um teto errado faz o nível do "
            "painel cair acima ou abaixo do nível da unidade."
        ),
        "cap_volume": "Convertido da escala da unidade, que conta até o teto acima.",
    },
}

SUGESTOES = (
    Sugestao("entradas", "CD", "cd"),
    Sugestao("entradas", "Coax 1", "coax1"),
    Sugestao("entradas", "Optica 1", "opt1"),
    Sugestao("entradas", "XLR", "bal_xlr"),
    Sugestao("entradas", "Bluetooth", "bluetooth"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the unit."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Rotel(Driver):
    """One Rotel unit, asked and commanded one exclamation mark at a time."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Amplificador Rotel", "en": "Rotel amplifier"},
        categoria="amplificador",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TECLA,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PARAR,
            ACAO_PROXIMA,
            ACAO_ANTERIOR,
        ),
        teclas=tuple(TECLAS),
        config_campos=(
            Campo(
                nome=CAMPO_VOLUME_MAXIMO,
                tipo=TipoCampo.INTEIRO,
                padrao=str(VOLUME_MAXIMO_PADRAO),
            ),
        ),
        # The unit announces nothing this hub can claim, so it is found by the range sweep,
        # which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Rotel",
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
            await self._mandar("power_on")
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._mandar("power_off")
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._mandar(f"vol_{self._para_o_aparelho(valor):02d}")
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._mandar("mute_on" if valor else "mute_off")
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            entrada = _palavra(valor)
            if entrada is None:
                return INVALID_VALUE
            await self._mandar(entrada)
            self._defina(fonte=entrada)
            return None
        if acao == ACAO_TECLA:
            comando = TECLAS.get(valor) if isinstance(valor, str) else None
            if comando is None:
                return INVALID_VALUE
            await self._mandar(comando)
            return None
        if acao in TRANSPORTE:
            await self._mandar(TRANSPORTE[acao])
            self._defina(reproduzindo=_tocando(acao))
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: the four questions of the unit, read by the name each answer carries."""
        lidas = await self._perguntar(
            (f"{NOME_ENERGIA}?", f"{NOME_VOLUME}?", f"{NOME_MUDO}?", f"{NOME_FONTE}?"),
            esperados=(NOME_ENERGIA, NOME_VOLUME, NOME_MUDO, NOME_FONTE),
        )
        energia = lidas.get(NOME_ENERGIA, "")
        self._defina(
            online=True,
            ligado=None if not energia else energia == LIGADO,
            volume=self._do_aparelho(lidas.get(NOME_VOLUME, "")),
            mudo=_sim_ou_nao(lidas.get(NOME_MUDO, "")),
            fonte=lidas.get(NOME_FONTE) or None,
            detalhe="",
        )

    async def _mandar(self, comando: str) -> None:
        """A command the unit answers with its new state, which the next poll reads anyway."""
        _leitor, escritor = await self._conectar()
        self._fio.enviado(comando)
        try:
            escritor.write(comando.encode("ascii") + FIM_DO_COMANDO)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(comando, erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _perguntar(
        self, comandos: tuple[str, ...], *, esperados: tuple[str, ...]
    ) -> dict[str, str]:
        """The questions go out together and the answers are read by name, not by order.

        The unit pushes the same lines on its own when the front panel moves, so an answer
        that arrives without a question is kept just the same and nothing is read positionally.
        """
        leitor, escritor = await self._conectar()
        pedido = "".join(f"{comando}!" for comando in comandos)
        self._fio.enviado(" ".join(comandos), rotina=True)
        try:
            escritor.write(pedido.encode("ascii"))
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(pedido, erro)
            raise _Falha(EQ_OFFLINE) from erro
        lidas: dict[str, str] = {}
        for _ in range(RESPOSTAS_ATE_DESISTIR):
            if all(nome in lidas for nome in esperados):
                return lidas
            resposta = await self._ler_uma()
            casado = _RESPOSTA.match(resposta)
            if casado is None:
                log.debug("%s: line of the unit dropped: %s", self._id(), resposta)
                continue
            lidas[casado.group("nome")] = casado.group("valor").strip()
        if not any(nome in lidas for nome in esperados):
            raise _Falha(ERRO_APARELHO)
        return lidas

    async def _ler_uma(self) -> str:
        """One answer, which ends at the dollar sign of today or the exclamation of before."""
        leitor, _escritor = await self._conectar()
        saida = bytearray()
        try:
            async with asyncio.timeout(RESPOSTA_S):
                while len(saida) < LEITURA_MAXIMA:
                    pedaco = await leitor.read(1)
                    if not pedaco:
                        raise _Falha(EQ_OFFLINE)
                    if pedaco.decode("ascii", errors="replace") in FINS_DA_RESPOSTA:
                        lida = saida.decode("ascii", errors="replace")
                        self._fio.recebido(lida, rotina=True)
                        return lida
                    saida += pedaco
        except TimeoutError:
            self._fio.falhou("read", "no answer")
            raise _Falha(ERRO_APARELHO) from None
        except (OSError, ValueError) as erro:
            self._fio.falhou("read", erro)
            raise _Falha(EQ_OFFLINE) from erro
        raise _Falha(ERRO_APARELHO)

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

    def _teto(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_VOLUME_MAXIMO, "").strip()
        if not _SO_DIGITOS.match(bruto):
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
            escritor.write(f"{NOME_MAC}?".encode("ascii") + FIM_DO_COMANDO)
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
        lida = bruto.decode("ascii", errors="replace").strip("$! \r\n")
        casado = _RESPOSTA.match(lida)
        if casado is None or casado.group("nome") != NOME_MAC:
            return None
        return casado.group("valor").strip().lower() or None


def _palavra(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    limpo = valor.strip().lower()
    return limpo if _PALAVRA.match(limpo) else None


def _sim_ou_nao(palavra: str) -> bool | None:
    if palavra == "on":
        return True
    return False if palavra == "off" else None


def _tocando(acao: str) -> bool | None:
    if acao == ACAO_TOCAR:
        return True
    return False if acao in (ACAO_PAUSAR, ACAO_PARAR) else None
