# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""AAT digital matrix amplifier, one registration per ZONE,.

What the amplifier is: a matrix with four to eight inputs routed to two to six zones, each
zone with its own input, volume, mute, standby, bass, treble, balance and pre-amp gain. A
registration here is ONE ZONE, the way a registration of a Yamaha is one zone of the receiver:
each zone takes its own number on the app, and the address plus the zone is what
the key of the registration carries.

What the wire is:

- TCP, one line at a time, ASCII, case insensitive. The frame is [t<seq> CMD par1 par2] and
the answer is [r<seq> CMD par1 par2], with the answer repeating the sequence of the
question. The manual allows the same sequence on consecutive messages, so this driver sends
a constant one and matches the answer by the COMMAND, which is what actually distinguishes
two answers on one socket;
- the amplifier also pushes unsolicited [n<seq>...] lines when a knob on its front panel
moves. They are read and dropped, because the poll is what publishes state and a line that
arrives between a question and its answer must not be read as the answer;
- port 5000 is fixed and a second port is configurable on the amplifier between 1024 and
65535, so the registration carries a port and defaults to 5000;
- GETALL answers model, firmware, power and then input, volume, mute, bass, treble, balance
and pre-amp of EVERY zone in one round trip. It is the whole poll of this driver: six zones
read with one question instead of forty two;
- volume is 0 to 87 in steps of exactly 1 dB, where 0 is silence, so the 0 to 100
is converted in one place in both directions;
- the amplifier has NO serial number and NO uuid on the wire: MODEL is the only thing it says
about itself and two of the same model in one house answer the same word. So identificar
answers nothing and the identity is what the integrator writes, which allows and
which the matrix of drivers records.
"""

import asyncio
import logging
import re

from iphub.config import ip_literal
from iphub.drivers import fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.aat")

TIPO = "multiroom_aat"

# 5000 is fixed on every firmware and the second port is the one the installer moves when
# something else on the network already holds 5000; the registration carries whichever is in
# use, and the default is the one that always exists.
PORTA_PADRAO = 5000
PORTA_MINIMA = 1
PORTA_MAXIMA = 65535

CAMPO_PORTA = "porta"
CAMPO_ZONA = "zona"

ZONA_MINIMA = 1
ZONA_MAXIMA = 6

ENTRADA_MINIMA = 1
ENTRADA_MAXIMA = 8

# The volume of the amplifier is 0 to 87 in steps of exactly 1 dB and 0 is silence, and
# fixes the scale of the bus at 0 to 100; converting in one place is what keeps the
# panel and the app from ever seeing a number of the amplifier.
VOLUME_DO_APARELHO = 87
VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100

# The tone controls are 0 to 14 with 7 at the centre, and the balance is 0 to 20 with 10 at
# the centre; both are read and written raw by comando_extra and never by a capability of
# which has no vocabulary for them.
TOM_MAXIMO = 14
BALANCO_MAXIMO = 20
PREAMP_MAXIMO = 7

SEQUENCIAL = "001"
ABRE = "["
FECHA = "]"
TERMINADOR = b"\r"

CONEXAO_S = 3.0
RESPOSTA_S = 3.0
LINHA_MAXIMA = 4 * 1024
LINHAS_ATE_DESISTIR = 8
FALHAS_ATE_OFFLINE = 2

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
NAO_SUPORTADO = "nao_suportado"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TECLA = "tecla"
ACAO_EXTRA = "comando_extra"

# A key is a step the customer presses over and over, and the amplifier has
# a command of its own for each of them; sending the absolute value instead would read the
# level, add one and write it back, which is two round trips and a race with the front panel.
TECLAS_DA_ZONA = {
    "mais": "VOL+",
    "menos": "VOL-",
}

# The manual defines the frame of a REFUSAL nowhere.8 has a heading and no
# content, and the list that follows it starts at code 7. So anything that is not the answer
# to the command that was asked is a refusal of the device, and the driver says erro_aparelho
# instead of inventing a meaning for a code it cannot read.
_QUADRO = re.compile(r"\[\s*(?P<tipo>[trn])\s*(?P<seq>\d{3})\s+(?P<corpo>[^\]]*)\]")
_PALAVRA = re.compile(r"[A-Za-z0-9_+\-]{1,32}")
_INTEIRO = re.compile(r"-?\d{1,6}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

CMD_GETALL = "GETALL"
CMD_STANDBY_ON = "ZSTDBYON"
CMD_STANDBY_OFF = "ZSTDBYOFF"
CMD_VOLSET = "VOLSET"
CMD_MUTEON = "MUTEON"
CMD_MUTEOFF = "MUTEOFF"
CMD_INPSET = "INPSET"

# GETALL answers five fields of the product and then seven of each zone, in a fixed order
# that the manual prints; reading it by position is the only way, because no field is named on
# the wire. The count of zones is what is left after the five, divided by seven.
CABECALHO_DO_GETALL = 5
CAMPOS_POR_ZONA = 7
POSICAO_DA_ENTRADA = 0
POSICAO_DO_VOLUME = 1
POSICAO_DO_MUDO = 2
POSICAO_DO_POWER = 2


class Aat(Driver):
    """One zone of an AAT matrix amplifier over its TCP line protocol."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Multiroom AAT", "en": "AAT multiroom"},
        categoria="matriz",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TECLA,
            ACAO_EXTRA,
        ),
        teclas=tuple(TECLAS_DA_ZONA),
        config_campos=(
            Campo(nome=CAMPO_ZONA, tipo=TipoCampo.INTEIRO, obrigatorio=True, padrao="1"),
            Campo(nome=CAMPO_PORTA, tipo=TipoCampo.INTEIRO, padrao=str(PORTA_PADRAO)),
        ),
        # The amplifier answers no discovery of any kind, so a sweep never finds it and
        # the integrator registers it by the address the installer gave it.
        descoberta=Descoberta(),
        sugestoes=tuple(
            Sugestao("entradas", f"Entrada {numero}", str(numero))
            for numero in range(ENTRADA_MINIMA, 5)
        ),
        textos={
            "en": {
                "descricao": (
                    "AAT digital matrix amplifier over TCP. One registration is ONE ZONE of "
                    "the amplifier, so a PMR of six zones is six registrations of this type "
                    "at the same address, each with its own zone number and its own number "
                    "on the app."
                ),
                "preparo": (
                    "Turn the rear switch of the amplifier on, or it takes no command. Give it a "
                    "fixed address in the installer menu (or reserve one on the router). The TCP "
                    "port is 5000 from the factory; the second port is 1024 unless the installer "
                    "moved it. One zone is one registration."
                ),
                "campo_zona": (
                    "Number of the zone of the amplifier this registration commands, 1 to 6. "
                    "The zones are numbered on the back of the product."
                ),
                "campo_porta": (
                    "TCP port of the amplifier. It is 5000 unless the second port was moved "
                    "in the installer menu of the product."
                ),
                "cap_fonte": (
                    "The value is the number of the input, 1 to 8. On a model with digital "
                    "inputs they are 5 to 8 and the analogue ones are 1 to 4; the manual of "
                    "the product says how many it has."
                ),
                "cap_ligar": (
                    "Turning a zone on and off is the standby of the zone, which puts the "
                    "amplifier of that zone at zero power and mutes its line and sub outputs. "
                    "The PMA-1 and the PMA-2 have no internal power amplifier, so the zone "
                    "standby does nothing on them."
                ),
                "cap_tecla": (
                    "The two keys are the steps of volume of the zone, which is what a "
                    "customer presses over and over."
                ),
                "cap_comando_extra": (
                    "The command travels inside the frame of the protocol, so write only the "
                    "command and its parameters: BASSSET 1 14, TREBLEGET 2, BALSET 1 10, "
                    "PREAMPSET 1 7, MUTEALL, ZTONALL, MODEL, VER."
                ),
                "lista_entradas": "The number of the input, 1 to 8.",
            },
            "pt": {
                "descricao": (
                    "Amplificador matricial digital AAT por TCP. Um cadastro é UMA ZONA do "
                    "amplificador, então um PMR de seis zonas são seis cadastros deste tipo "
                    "no mesmo endereço, cada um com o número de zona dele e o número dele no "
                    "app."
                ),
                "preparo": (
                    "Ligue a chave traseira do amplificador, ou ele não aceita comando. Dê um "
                    "endereço fixo a ele no menu de instalador (ou reserve um no roteador). A "
                    "porta TCP é 5000 de fábrica; a segunda porta é 1024, a menos que o instalador "
                    "a tenha mudado. Uma zona é um cadastro."
                ),
                "campo_zona": (
                    "Número da zona do amplificador que este cadastro comanda, 1 a 6. As "
                    "zonas são numeradas na traseira do produto."
                ),
                "campo_porta": (
                    "Porta TCP do amplificador. É 5000, a menos que a segunda porta tenha "
                    "sido trocada no menu de instalador do produto."
                ),
                "cap_fonte": (
                    "O valor é o número da entrada, 1 a 8. Num modelo com entradas digitais "
                    "elas são 5 a 8 e as analógicas são 1 a 4; o manual do produto diz "
                    "quantas ele tem."
                ),
                "cap_ligar": (
                    "Ligar e desligar uma zona é o standby dela, que põe o amplificador "
                    "daquela zona em potência zero e muta as saídas de linha e de subwoofer. "
                    "O PMA-1 e o PMA-2 não têm amplificador de potência interno, então o "
                    "standby de zona não faz nada neles."
                ),
                "cap_tecla": (
                    "As duas teclas são os passos de volume da zona, que é o que um cliente "
                    "aperta muitas vezes seguidas."
                ),
                "cap_comando_extra": (
                    "O comando viaja dentro do quadro do protocolo, então escreva só o "
                    "comando e os parâmetros dele: BASSSET 1 14, TREBLEGET 2, BALSET 1 10, "
                    "PREAMPSET 1 7, MUTEALL, ZTONALL, MODEL, VER."
                ),
                "lista_entradas": "O número da entrada, 1 a 8.",
            },
        },
        marca="AAT",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._leitor: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._trava = asyncio.Lock()
        self._falhas = 0
        self._modelo = ""
        self._fio = fio.Fio(log, self._id())

    async def parar(self) -> None:
        await self._descartar()

    async def atualizar(self) -> None:
        """One poll: GETALL, which carries every zone of the product in one round trip."""
        try:
            async with self._trava:
                corpo = await self._perguntar(CMD_GETALL, ())
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                await self._descartar()
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        self._publicar(corpo)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            async with self._trava:
                return await self._agir(acao, valor)
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                await self._descartar()
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        zona = self._zona()
        if zona is None:
            return INVALID_VALUE
        if acao == ACAO_LIGAR:
            await self._perguntar(CMD_STANDBY_OFF, (str(zona),))
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._perguntar(CMD_STANDBY_ON, (str(zona),))
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
                return INVALID_VALUE
            await self._perguntar(CMD_VOLSET, (str(zona), str(_para_o_aparelho(valor))))
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._perguntar(CMD_MUTEON if valor else CMD_MUTEOFF, (str(zona),))
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            entrada = _entrada_valida(valor)
            if entrada is None:
                return INVALID_VALUE
            await self._perguntar(CMD_INPSET, (str(zona), str(entrada)))
            self._defina(fonte=str(entrada))
            return None
        if acao == ACAO_TECLA:
            comando = TECLAS_DA_ZONA.get(valor) if isinstance(valor, str) else None
            if comando is None:
                return NAO_SUPORTADO
            await self._perguntar(comando, (str(zona),))
            return None
        if acao == ACAO_EXTRA:
            return await self._extra(valor)
        return await super().executar(acao, valor)

    async def _extra(self, valor: object) -> str | None:
        """A command of the manual, written whole, inside the frame this driver builds.

        The frame and the sequence are of the protocol and never of the operator, so what
        he writes is the command and its parameters and nothing else; a bracket or a control
        character in there is a frame he is building by hand, and it is refused.
        """
        if not isinstance(valor, str) or _CONTROLE.search(valor):
            # Split would swallow a carriage return and send the two halves as a
            # command and a parameter, which is a frame the operator was building by hand
            # arriving at the amplifier as something else. It is refused whole.
            return INVALID_VALUE
        partes = valor.strip().split()
        if not partes or not all(_PALAVRA.fullmatch(parte) for parte in partes):
            return INVALID_VALUE
        await self._perguntar(partes[0].upper(), tuple(partes[1:]))
        return None

    def _publicar(self, corpo: tuple[str, ...]) -> None:
        """The answer of GETALL, read by position, for the zone of this registration."""
        if len(corpo) < CABECALHO_DO_GETALL:
            log.warning("%s: the GETALL of this model is shorter than its header", self._id())
            self._falhar(ERRO_APARELHO)
            return
        self._modelo = corpo[0]
        zona = self._zona()
        campos = _campos_da_zona(corpo, zona)
        if campos is None:
            # A zone the product does not have is a registration nothing can reach, and
            # saying so is what sends the integrator to the field instead of to the network.
            log.warning("%s: this %s has no zone %s", self._id(), self._modelo, zona)
            self._falhar(INVALID_VALUE)
            return
        entrada = _inteiro(campos[POSICAO_DA_ENTRADA])
        bruto = _inteiro(campos[POSICAO_DO_VOLUME])
        self._defina(
            online=True,
            ligado=_ligado_de(corpo[POSICAO_DO_POWER]),
            volume=None if bruto is None else _do_aparelho(bruto),
            mudo=_ligado_de(campos[POSICAO_DO_MUDO]),
            fonte=None if entrada is None else str(entrada),
            detalhe="",
        )

    async def _perguntar(self, comando: str, parametros: tuple[str, ...]) -> tuple[str, ...]:
        """One frame out and the answer of THAT command back, unsolicited lines dropped."""
        leitor, escritor = await self._conectar()
        linha = f"{ABRE}t{SEQUENCIAL} {' '.join((comando, *parametros)).strip()}{FECHA}"
        rotina = comando == CMD_GETALL
        self._fio.enviado(linha, rotina=rotina)
        try:
            escritor.write(linha.encode("ascii") + TERMINADOR)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(linha, erro)
            raise _Falha(EQ_OFFLINE) from erro
        for _ in range(LINHAS_ATE_DESISTIR):
            try:
                lida = await _ler(leitor)
            except _Falha as falha:
                self._fio.falhou(linha, falha.codigo)
                raise
            achado = _quadro_de(lida)
            if achado is None:
                continue
            tipo, corpo = achado
            self._fio.recebido(lida, rotina=rotina and tipo == "r")
            if tipo != "r":
                # The amplifier pushes a line of its own when the front panel moves, and
                # reading it as the answer of this question would publish the wrong zone.
                log.debug("%s: unsolicited line dropped: %s", self._id(), corpo)
                continue
            if corpo and corpo[0].upper() == comando.upper():
                return corpo[1:]
            log.warning("%s: %s answered %s", self._id(), comando, " ".join(corpo))
            raise _Falha(ERRO_APARELHO)
        raise _Falha(ERRO_APARELHO)

    async def _conectar(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        leitor, escritor = self._leitor, self._escritor
        if leitor is not None and escritor is not None and not escritor.is_closing():
            return leitor, escritor
        endereco = ip_literal(self.cadastro.ip)
        porta = self._porta()
        if endereco is None or porta is None:
            raise _Falha(EQ_OFFLINE)
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(endereco, porta)
        except (OSError, TimeoutError) as erro:
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
        self._falhas += 1
        if self._falhas < FALHAS_ATE_OFFLINE and self.estado().online:
            self._defina(detalhe=codigo)
            return
        self._defina(online=False, detalhe=codigo)

    def _zona(self) -> int | None:
        bruto = _inteiro(self.cadastro.campos.get(CAMPO_ZONA, ""))
        return bruto if bruto is not None and ZONA_MINIMA <= bruto <= ZONA_MAXIMA else None

    def _porta(self) -> int | None:
        bruto = _inteiro(self.cadastro.campos.get(CAMPO_PORTA, "") or str(PORTA_PADRAO))
        return bruto if bruto is not None and PORTA_MINIMA <= bruto <= PORTA_MAXIMA else None

    def _id(self) -> str:
        return self.cadastro.identidade or self.cadastro.ip


class _Falha(Exception):
    """A stable code travelling as an exception inside this driver."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


async def _ler(leitor: asyncio.StreamReader) -> str:
    """One line of the amplifier, or a stable code for a socket that stopped answering."""
    try:
        async with asyncio.timeout(RESPOSTA_S):
            bruto = await leitor.readuntil(TERMINADOR)
    except asyncio.LimitOverrunError as erro:
        raise _Falha(ERRO_APARELHO) from erro
    except (OSError, TimeoutError, asyncio.IncompleteReadError) as erro:
        raise _Falha(EQ_OFFLINE) from erro
    if len(bruto) > LINHA_MAXIMA:
        raise _Falha(ERRO_APARELHO)
    return bruto.decode("ascii", errors="replace")


def _quadro_de(linha: str) -> tuple[str, tuple[str, ...]] | None:
    """The type and the words of a frame, or None for a line that is not one."""
    achado = _QUADRO.search(linha)
    if achado is None:
        return None
    return achado.group("tipo").lower(), tuple(achado.group("corpo").split())


def _campos_da_zona(corpo: tuple[str, ...], zona: int | None) -> tuple[str, ...] | None:
    """The seven fields of that zone inside the answer of GETALL, or None when it has none."""
    if zona is None:
        return None
    inicio = CABECALHO_DO_GETALL + (zona - ZONA_MINIMA) * CAMPOS_POR_ZONA
    fim = inicio + CAMPOS_POR_ZONA
    return corpo[inicio:fim] if fim <= len(corpo) else None


def _ligado_de(palavra: str) -> bool | None:
    """ON and OFF of the amplifier as the boolean, and None for anything else."""
    limpo = palavra.strip().upper()
    if limpo == "ON":
        return True
    return False if limpo == "OFF" else None


def _entrada_valida(valor: object) -> int | None:
    numero = _inteiro(valor if isinstance(valor, str) else "")
    if isinstance(valor, int) and not isinstance(valor, bool):
        numero = valor
    if numero is None or not ENTRADA_MINIMA <= numero <= ENTRADA_MAXIMA:
        return None
    return numero


def _inteiro(bruto: object) -> int | None:
    if not isinstance(bruto, str) or not _INTEIRO.fullmatch(bruto.strip()):
        return None
    return int(bruto.strip())


def _do_aparelho(bruto: int) -> int:
    """The 0 to 87 of the amplifier as the 0 to 100."""
    preso = max(0, min(VOLUME_DO_APARELHO, bruto))
    return round(preso * VOLUME_MAXIMO / VOLUME_DO_APARELHO)


def _para_o_aparelho(valor: int) -> int:
    """The 0 to 100 as the 0 to 87 of the amplifier."""
    return round(valor * VOLUME_DO_APARELHO / VOLUME_MAXIMO)
