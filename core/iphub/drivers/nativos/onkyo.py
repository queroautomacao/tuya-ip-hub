# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Onkyo and Integra receiver over eISCP, the pushing native driver of sections 6 and 14.

Two layers and two IP protocols behind one port number, which is the first thing this
protocol costs whoever writes it:

- control is ONE long TCP connection the receiver pushes state on. Nobody asks it to report:
a knob turned on the front panel writes a frame on the same socket. Opening and closing a
socket per poll would lose every unsolicited frame and would fight the remote of the room,
because the receiver accepts few connections at a time;
- discovery and identity are UDP on 60128, and the TCP port to connect to is the one INSIDE
the answer of that question, not the 60128 the question went to;
- the frame is a 16 byte binary header plus the ISCP text, and the length comes from the
header. The stream is never split by line: a title carries bytes that look like a
terminator, and there are three of them (\\r\\n is sent, \\x1a\\r\\n or \\r\\n comes back
over TCP, \\x19\\r\\n comes back over UDP);
- the discovery question goes out twice, as unit "x" and as unit "p", because a unit of the
sister brand answers only the second one and is lost in silence otherwise;
- the identity is the MAC inside that same answer, which is also exactly what the wake on LAN
packet needs, so the registration asks for no MAC and for no port;
- the volume scale is 50, 80, 100 or 200 steps depending on the model and NO question reveals
which, so it is the one field of the registration. Guessing it is wrong by a factor of four;
- nothing confirms a command. The receiver reports state changes, never echoes a command and
answers nothing at all to a command it does not have, so rereading the state is the only
verification there is, which is what already does;
- right after a power on the state that comes back is stale, so the driver asks again about
four seconds later, in one place;
- the transport frame reports stop on a receiver that is playing an HDMI source, so
reproduzindo only exists while a network input is the active one, and WHICH code
is a network input changes by model, so it is the second field of the registration;
- a receiver that takes the connection and answers nothing is not an online receiver: a poll
no frame at all came back on is a lost poll, and two in a row are an offline. A code the
model does not have answering nothing is not, which is why the whole poll is the measure;
- a receiver in standby without Network Standby does not answer IP at all, so ligar sends the
magic packet to the registered MAC before it tries to talk.

The list of inputs and of sound modes is the one of the registration: the receiver only tells
its own in an XML document, and handing a document of a device on the LAN to a parser is what
this repository refuses to do, so Estado.fontes stays empty and the codes come from the lists.
"""

import asyncio
import logging
import re
import struct
from contextlib import suppress
from dataclasses import dataclass

from iphub.config import ip_literal
from iphub.drivers import fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.onkyo")

TIPO = "receiver_onkyo"

# 60128 is the UDP port the interview is asked on and the fallback for control; the TCP
# port of a given unit is the one its answer carries, and only a silent unit falls back here.
PORTA_PADRAO = 60128
# The port of the magic packet of wake on LAN, which is not a port of this protocol.
PORTA_WOL = 9

# The gestor gives a call into a driver half of the poll interval, so the three deadlines
# of one poll (interview, connection, answer) have to fit inside it with room to spare.
PRAZO_UDP_S = 1.0
TEMPO_LIMITE_S = 2.0
# The protocol document promises 50 ms and the measured floor is 300 ms, so a tight
# deadline here would read a healthy receiver as a silent one.
PRAZO_RESPOSTA_S = 1.0
# The state read right after a power on is stale, so the driver asks again once the
# receiver has finished waking up.
ATRASO_APOS_LIGAR_S = 4.0

FALHAS_ATE_OFFLINE = 2

# The eISCP frame: magic, header size, data size, version, reserved.
CABECALHO = struct.Struct("! 4s I I B 3s")
MAGICO = b"ISCP"
TAMANHO_CABECALHO = 16
VERSAO = 1
RESERVADO = b"\x00\x00\x00"
INICIO = b"!"
UNIDADE_RECEIVER = b"1"
UNIDADE_DESCOBERTA = b"x"
UNIDADE_PIONEER = b"p"

# What is sent ends in CRLF, what comes back may end in SUB plus CRLF, in CRLF alone or,
# from the discovery, in EOM plus CRLF; a reader that demands the SUB drops real frames.
FIM_ENVIO = b"\r\n"
FIM_LEITURA = b"\x1a\x19\r\n"

# A receiver on the LAN must never be able to size the memory of this daemon, and the
# frame carries its own length, so the ceiling is checked before a single byte is read.
CABECALHO_MAXIMO = 64
DADO_MAXIMO = 8 * 1024
DATAGRAMA_MAXIMO = 8 * 1024
TITULO_MAXIMO = 64
MODELO_MAXIMO = 64
VALOR_MAXIMO = 16

TAMANHO_CODIGO = 3
# The "!" plus the unit byte plus the three letters of the code.
TAMANHO_MINIMO = 5

CODIGO_ENERGIA = "PWR"
CODIGO_VOLUME = "MVL"
CODIGO_MUDO = "AMT"
CODIGO_FONTE = "SLI"
CODIGO_MODO = "LMD"
CODIGO_ATALHO = "PRS"
CODIGO_TRANSPORTE = "NST"
CODIGO_TITULO = "NTI"
CODIGO_REDE = "NTC"
CODIGO_MENU = "OSD"
CODIGO_DESCOBERTA = "ECN"

PERGUNTA = "QSTN"
PERGUNTA_ECN = f"{CODIGO_DESCOBERTA}{PERGUNTA}".encode("ascii")
PERGUNTAS = (CODIGO_ENERGIA, CODIGO_VOLUME, CODIGO_MUDO, CODIGO_FONTE, CODIGO_MODO)
# These two only mean anything on a network input, and a model without the network board
# answers neither, which would cost the poll one deadline of silence every ten seconds.
PERGUNTAS_DE_REDE = (CODIGO_TRANSPORTE, CODIGO_TITULO)

LIGADO = "01"
DESLIGADO = "00"
# PWRALL is All Zone Standby and would take down the zones this hub does not control,
# and AMTTG toggles, which inverts the result of every report that arrived late; the driver
# already knows the state, so it writes the value it wants.
TOCANDO = "P"
# A real value of the protocol: supported, with no value right now.
NAO_DISPONIVEL = "N/A"

# The transport commands act only on the network and USB inputs, and the transport frame
# answers stop on any other one, which is the defect describes for the speakers.
# WHICH code is one of those is a fact of the model, exactly like the code of every other
# input, so it is a field and never a list this file carries: on a model where 2B is another
# input a hard coded set would claim the receiver plays on something that is not a player.
CAMPO_REDE = "entradas_de_rede"
ENTRADAS_DE_REDE_PADRAO = "2B,29"

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
CAMPO_ESCALA = "escala_volume"
# The wire carries the volume in hexadecimal and the top of the scale is a fact of the
# model that no question answers, so the registration says it and the conversion to the 0 to
# 100 lives in one place.
ESCALAS = (50, 80, 100, 200)
ESCALA_PADRAO = 50

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_MODO = "modo"
ACAO_ATALHO = "atalho"
ACAO_TECLA = "tecla"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"
ACAO_EXTRA = "comando_extra"

# The transport of the network player, which is one code with one word each.
TRANSPORTE = {
    ACAO_TOCAR: "PLAY",
    ACAO_PAUSAR: "PAUSE",
    ACAO_PARAR: "STOP",
    ACAO_PROXIMA: "TRUP",
    ACAO_ANTERIOR: "TRDN",
}

# The words land on two different subsystems of the receiver, the on screen
# menu and the network player, plus the volume; the driver is the only place that knows which.
# The word "guia" is not here on purpose: the only guide command of this protocol is sent to
# the TV over HDMI and confirms nothing, so declaring it would put a dead key on the panel.
TECLAS = {
    "mais": (CODIGO_VOLUME, "UP"),
    "menos": (CODIGO_VOLUME, "DOWN"),
    "cima": (CODIGO_MENU, "UP"),
    "baixo": (CODIGO_MENU, "DOWN"),
    "esquerda": (CODIGO_MENU, "LEFT"),
    "direita": (CODIGO_MENU, "RIGHT"),
    "ok": (CODIGO_MENU, "ENTER"),
    "sair": (CODIGO_MENU, "EXIT"),
    "menu": (CODIGO_MENU, "MENU"),
    "inicio": (CODIGO_MENU, "HOME"),
    "voltar": (CODIGO_REDE, "RETURN"),
    "info": (CODIGO_REDE, "DISPLAY"),
    "play_pause": (CODIGO_REDE, "P/P"),
    "proxima": (CODIGO_REDE, "TRUP"),
    "anterior": (CODIGO_REDE, "TRDN"),
    "canal_mais": (CODIGO_REDE, "CHUP"),
    "canal_menos": (CODIGO_REDE, "CHDN"),
    **{f"digito_{n}": (CODIGO_REDE, str(n)) for n in range(10)},
}

# A value of a list of the registration decides bytes on a socket, so it is checked
# against a closed alphabet before anything is written: a value carrying a terminator would
# close the frame and write a second command that nobody wrote in this file.
_VALOR = re.compile(r"[A-Z0-9]{1,8}")
_EXTRA = re.compile(r"[A-Z0-9]{3}[A-Z0-9/:+._-]{0,24}")
_NO_FIO = re.compile(r"[A-Z0-9]{3}[A-Z0-9/:+._-]{0,32}")
_MAC = re.compile(r"[0-9A-Fa-f]{12}")
_NUMERO = re.compile(r"[0-9]{1,3}")
_HEXADECIMAL = re.compile(r"[0-9A-Fa-f]{1,4}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
# The answer of the interview: model, the TCP port of control, the area and the MAC.
# The identifier is taken WHOLE and only then judged as a MAC. A group of hexadecimal
# characters alone matches the head of an identifier that is not one and hands the driver a
# truncated string, which is a MAC the magic packet then drops in silence.
_ECN = re.compile(
    r"(?P<modelo>[^/]{1,64})/(?P<porta>[0-9]{5})/(?P<area>[A-Z]{2})"
    r"(?:/(?P<identidade>[^/\s]{1,32}))?"
)

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

# The code of an input and of a sound mode is a hexadecimal pair that nobody memorises,
# and the same pair means another thing on another model, so the driver offers the usual ones
# and the integrator renames them and fixes what his receiver really answers. The set is short
# on purpose: what is suggested here is what a fresh registration carries into the profile of
# which fits 200 bytes, so a generous default would refuse the registration before
# the integrator typed anything. Four presets and not eight for the same reason, and the ones
# left out are the ones a receiver of this line answers to least often.
SUGESTOES = (
    Sugestao("entradas", "TV", "12"),
    Sugestao("entradas", "Blu-ray", "10"),
    Sugestao("entradas", "Cabo / Sat", "01"),
    Sugestao("entradas", "Game", "02"),
    Sugestao("entradas", "CD", "23"),
    Sugestao("entradas", "Rede", "2B"),
    Sugestao("entradas", "Bluetooth", "2E"),
    Sugestao("modos", "Estereo", "00"),
    Sugestao("modos", "Direto", "01"),
    Sugestao("modos", "Surround", "02"),
    Sugestao("modos", "Pure Audio", "11"),
    *(Sugestao("atalhos", f"Preset {n}", f"{n:02X}") for n in range(1, 5)),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Onkyo and Integra receiver over eISCP. It holds one long connection the receiver "
            "pushes state on, asks the network of the receiver itself for the control port "
            "and for the identity, and needs no port and no MAC in the registration."
        ),
        "preparo": (
            "Turn Network Standby on (Setup, Hardware, Power Management, Network Standby), or the "
            "receiver only wakes by Wake on LAN and not always. Look up the volume scale of the "
            "model in its manual (50, 80, 100 or 200 steps) for the volume scale field. Reserve "
            "its address on the router."
        ),
        "campo_escala_volume": (
            "Steps the receiver takes from the lowest to the highest volume: 50, 80, 100 or "
            "200. It is printed nowhere and no command asks it, so it is read from the manual "
            "or found by ear. The wrong value makes the bar of the panel end at a quarter of "
            "the way or saturate in the middle."
        ),
        "campo_entradas_de_rede": (
            "The codes of the inputs the network player of this receiver drives, separated by "
            "a comma: 2B and 29 on most models. Only on those does the receiver say whether it "
            "plays and what the title is; on every other input it answers stop while it plays, "
            "so a code listed here by mistake makes the panel show a transport that lies. "
            "Leave it empty on a receiver with no network board."
        ),
        "cap_ligar": (
            "A receiver in standby only answers the network with Network Standby on. With it "
            "off, the hub sends a wake on LAN packet to the MAC that is its identity and then "
            "tries to talk, which works while the receiver is still known on the segment."
        ),
        "cap_fonte": (
            "The value is the code of the input in hexadecimal, with no SLI in front: 12 for "
            "TV, 10 for Blu-ray, 2B for the network. The same code means another input on "
            "another model, so the list is checked against the receiver."
        ),
        "cap_modo": (
            "The value is the code of the sound mode in hexadecimal, with no LMD in front: 00 "
            "stereo, 01 direct, 80 Dolby Surround, FF auto."
        ),
        "cap_atalho": (
            "A shortcut is a preset of the tuner, written as its number in hexadecimal, 01 to "
            "28. It only acts while the active input is FM, AM or DAB."
        ),
        "cap_tecla": (
            "The arrows, enter, exit, menu and home walk the menu of the receiver on the "
            "screen; the digits, the channel keys and the transport ones only act on the "
            "network and USB inputs."
        ),
        "cap_comando_extra": (
            "Any command of the eISCP chart written whole, the three letters of the code plus "
            "its parameter: DIF01, PRS02, NTCPLAY. It goes to the receiver as it is."
        ),
        "lista_entradas": "The hexadecimal code of the input: 12 is TV, 2B is the network.",
        "lista_atalhos": "The preset of the tuner in hexadecimal: 01 is the first one.",
        "lista_modos": "The hexadecimal code of the sound mode: 00 is stereo.",
    },
    "pt": {
        "descricao": (
            "Receiver Onkyo e Integra por eISCP. Ele mantém uma conexão longa em que o "
            "receiver empurra estado, pergunta à rede do próprio receiver a porta de controle "
            "e a identidade, e não precisa de porta nem de MAC no cadastro."
        ),
        "preparo": (
            "Ligue o Network Standby (Configuração, Hardware, Gerenciamento de energia, Network "
            "Standby), ou o receiver só acorda por Wake on LAN e nem sempre. Procure no manual do "
            "modelo a escala de volume (50, 80, 100 ou 200 passos) para o campo de escala de "
            "volume. Reserve o endereço dele no roteador."
        ),
        "campo_escala_volume": (
            "Passos que o receiver dá do volume mais baixo ao mais alto: 50, 80, 100 ou 200. "
            "Ele não está escrito em lugar nenhum e comando nenhum o pergunta, então é lido no "
            "manual ou achado de ouvido. O valor errado faz a barra do painel terminar num "
            "quarto do caminho ou saturar no meio."
        ),
        "campo_entradas_de_rede": (
            "Os códigos das entradas que o tocador de rede deste receiver comanda, separados "
            "por vírgula: 2B e 29 na maioria dos modelos. Só nelas o receiver diz se está "
            "tocando e qual é o título; em toda outra ele responde parado enquanto toca, então "
            "um código listado aqui por engano faz o painel mostrar um transporte que mente. "
            "Deixe vazio num receiver sem placa de rede."
        ),
        "cap_ligar": (
            "Um receiver em standby só atende a rede com o Network Standby ligado. Com ele "
            "desligado, o hub manda um pacote de wake on LAN para o MAC que é a identidade "
            "dele e então tenta falar, o que funciona enquanto o receiver ainda é conhecido no "
            "segmento."
        ),
        "cap_fonte": (
            "O valor é o código da entrada em hexadecimal, sem o SLI na frente: 12 para TV, 10 "
            "para Blu-ray, 2B para a rede. O mesmo código significa outra entrada em outro "
            "modelo, então a lista é conferida contra o receiver."
        ),
        "cap_modo": (
            "O valor é o código do modo de som em hexadecimal, sem o LMD na frente: 00 "
            "estéreo, 01 direto, 80 Dolby Surround, FF automático."
        ),
        "cap_atalho": (
            "Um atalho é um preset do sintonizador, escrito como o número dele em hexadecimal, "
            "01 a 28. Ele só age enquanto a entrada ativa é FM, AM ou DAB."
        ),
        "cap_tecla": (
            "As setas, o ok, o sair, o menu e o início andam pelo menu do receiver na tela; os "
            "dígitos, as teclas de canal e as de transporte só agem nas entradas de rede e USB."
        ),
        "cap_comando_extra": (
            "Qualquer comando da tabela eISCP escrito inteiro, as três letras do código mais o "
            "parâmetro dele: DIF01, PRS02, NTCPLAY. Ele vai para o receiver como está."
        ),
        "lista_entradas": "O código hexadecimal da entrada: 12 é a TV, 2B é a rede.",
        "lista_atalhos": "O preset do sintonizador em hexadecimal: 01 é o primeiro.",
        "lista_modos": "O código hexadecimal do modo de som: 00 é estéreo.",
    },
}


@dataclass(frozen=True)
class Entrevista:
    """What the receiver answers to the discovery question, which is asked over UDP.

    porta is the TCP port of control the unit itself names, and identidade is its MAC.
    """

    modelo: str
    porta: int
    identidade: str


class _Falha(Exception):
    """A stable code on the way out of an exchange, so no exception escapes executar."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Onkyo(Driver):
    """Power, volume, mute, input, sound mode, presets, keys and network transport of one
    Onkyo or Integra receiver.
    """

    # This protocol has no credential of any kind, so says the inherited
    # autenticar is the correct one and the driver writes no method to refuse.
    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Onkyo / Integra", "en": "Onkyo / Integra receiver"},
        categoria="receiver",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_MODO,
            ACAO_ATALHO,
            ACAO_TECLA,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PARAR,
            ACAO_PROXIMA,
            ACAO_ANTERIOR,
            ACAO_EXTRA,
        ),
        teclas=tuple(TECLAS),
        descoberta=Descoberta(ssdp_fabricantes=("onkyo", "integra")),
        config_campos=(
            Campo(CAMPO_ESCALA, TipoCampo.INTEIRO, padrao=str(ESCALA_PADRAO)),
            Campo(CAMPO_REDE, TipoCampo.TEXTO, padrao=ENTRADAS_DE_REDE_PADRAO),
        ),
        textos=TEXTOS,
        marca="Onkyo",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._escala = escala_de(cadastro.campos)
        self._entradas_de_rede = entradas_de_rede_de(cadastro.campos)
        self._escritor: asyncio.StreamWriter | None = None
        self._ouvinte: asyncio.Task | None = None
        self._reconsulta: asyncio.Task | None = None
        # The poll, a command of the integrator and the requery after a power on write on
        # the same socket, and two frames interleaved on it are one frame the receiver drops.
        self._trava = asyncio.Lock()
        self._identidade: str | None = None
        self._falhas = 0
        # The answers of a poll arrive on the connection like any unsolicited frame, so
        # the poll waits for the last one it asked instead of guessing when they landed.
        self._aguardado: str | None = None
        self._chegou = asyncio.Event()
        # A receiver that takes the connection and goes quiet, because its firmware hung
        # or because the wifi dropped with no FIN, writes nothing and closes nothing, and a
        # poll that only watches the socket would call it online with a state of hours ago.
        # Counting frames is what tells a mute receiver from a model missing one function.
        self._lidos = 0
        self._quebrado: str | None = None

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The MAC the receiver answers over UDP, which is its identity here."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        entrevista = await entrevistar(endereco)
        return None if entrevista is None else _mac_de(entrevista.identidade)

    async def iniciar(self) -> None:
        """Opens the long connection. A receiver in standby with no network is simply not
        there yet, and the next poll is what dials again.
        """
        with suppress(_Falha):
            await self._conectar()

    async def parar(self) -> None:
        tarefa, self._reconsulta = self._reconsulta, None
        if tarefa is not None:
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa
        await self._desconectar()

    def conectado(self) -> bool:
        """Whether the connection the receiver pushes state on is up right now."""
        tarefa, escritor = self._ouvinte, self._escritor
        return (
            tarefa is not None
            and not tarefa.done()
            and escritor is not None
            and not escritor.is_closing()
        )

    def identidade_do_aparelho(self) -> str | None:
        """The MAC the last interview answered, which says the unit here is still ours.

        The interview runs again on every connection, so this is what answers at this
        address today and never a photograph of the first boot; an identifier that is not a
        MAC is dropped here instead of travelling as a truncated one,.
        """
        return self._identidade

    async def atualizar(self) -> None:
        try:
            await self._consultar()
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        # A poll that came back is the end of whatever the last connection died of.
        self._quebrado = None
        self._defina(online=True, detalhe="")

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._ligar()
        if acao == ACAO_DESLIGAR:
            await self._mandar(CODIGO_ENERGIA, DESLIGADO)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._da_lista(CODIGO_FONTE, valor, fonte=True)
        if acao == ACAO_MODO:
            return await self._da_lista(CODIGO_MODO, valor, lista="modos")
        if acao == ACAO_ATALHO:
            return await self._da_lista(CODIGO_ATALHO, valor)
        if acao == ACAO_TECLA:
            return await self._tecla(valor)
        if acao in TRANSPORTE:
            return await self._transporte(acao)
        if acao == ACAO_EXTRA:
            return await self._extra(valor)
        return await super().executar(acao, valor)

    async def _ligar(self) -> str | None:
        """A receiver without Network Standby does not answer IP at all, and the
        magic packet is the only door left; the MAC it needs is the identity.
        """
        if not self.conectado():
            await self._acordar()
        await self._mandar(CODIGO_ENERGIA, LIGADO)
        self._defina(ligado=True)
        self._reconsultar_depois()
        return None

    async def _trocar_volume(self, valor: object) -> str | None:
        # True is an int in Python, and a mute arriving where a volume fits would silence
        # a room; the type is checked before the range.
        if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
            return INVALID_VALUE
        await self._mandar(CODIGO_VOLUME, f"{_para_o_aparelho(valor, self._escala):02X}")
        self._defina(volume=valor)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        await self._mandar(CODIGO_MUDO, LIGADO if valor else DESLIGADO)
        self._defina(mudo=valor)
        return None

    async def _da_lista(
        self, codigo: str, valor: object, *, lista: str = "", fonte: bool = False
    ) -> str | None:
        """One value of a list of the registration behind the three letters of its code.

        The wire takes it in upper case, which is the alphabet of this protocol, and the state
        keeps the spelling the registration saved, which is what matches by.
        """
        if not isinstance(valor, str) or not _VALOR.fullmatch(valor.strip().upper()):
            return INVALID_VALUE
        palavra = valor.strip().upper()
        await self._mandar(codigo, palavra)
        if fonte:
            self._trocou_de_fonte(self._do_cadastro("entradas", palavra) or palavra)
        elif lista:
            self._defina(modo=self._do_cadastro(lista, palavra))
        return None

    async def _tecla(self, valor: object) -> str | None:
        alvo = TECLAS.get(valor) if isinstance(valor, str) else None
        if alvo is None:
            return INVALID_VALUE
        await self._mandar(*alvo)
        return None

    async def _transporte(self, acao: str) -> str | None:
        await self._mandar(CODIGO_REDE, TRANSPORTE[acao])
        # The transport frame of this receiver reports stop on anything that is not a
        # network input, so claiming it plays outside one would be the defect.
        if self._de_rede(self.estado().fonte):
            self._defina(reproduzindo=acao == ACAO_TOCAR)
        return None

    async def _extra(self, valor: object) -> str | None:
        """Any command of the chart, written whole: three letters of code plus a parameter."""
        if not isinstance(valor, str) or not _EXTRA.fullmatch(valor.strip().upper()):
            return INVALID_VALUE
        bruto = valor.strip().upper()
        await self._mandar(bruto[:TAMANHO_CODIGO], bruto[TAMANHO_CODIGO:])
        return None

    async def _consultar(self) -> None:
        """One poll: the short questions, plus the two of the player on a network input.

        A poll not one single frame came back on is a lost poll, because a receiver that took
        the connection and went quiet closes nothing and the socket alone would say online
        with a state of hours ago. One question with no answer is not: a model simply does not
        have every function, and the other four still prove the receiver is here.
        """
        perguntas = PERGUNTAS
        if self._de_rede(self.estado().fonte):
            perguntas += PERGUNTAS_DE_REDE
        antes = self._lidos
        for codigo in perguntas:
            await self._mandar(codigo, PERGUNTA)
        await self._aguardar(perguntas[-1])
        if self._lidos == antes:
            # A connection that died reading a frame this protocol cannot resume from is
            # a different fact from a receiver that says nothing, and the state
            # carries the difference instead of calling both the same silence.
            motivo, self._quebrado = self._quebrado, None
            raise _Falha(motivo or EQ_OFFLINE)

    async def _aguardar(self, codigo: str) -> None:
        """Waits for the answer to the last question, which arrives like any pushed frame.

        A model that does not have a function answers nothing to it, and that is not a fault
        of its own: what the poll then measures is whether ANY frame came back.
        """
        self._aguardado = codigo
        self._chegou.clear()
        try:
            async with asyncio.timeout(PRAZO_RESPOSTA_S):
                await self._chegou.wait()
        except TimeoutError:
            log.debug("%s: no answer to %s", self.cadastro.identidade, codigo)
        finally:
            self._aguardado = None

    def _reconsultar_depois(self) -> None:
        """The state read right after a power on is stale, so it is asked again."""
        anterior, self._reconsulta = self._reconsulta, None
        if anterior is not None:
            anterior.cancel()
        self._reconsulta = asyncio.create_task(self._reconsultar())

    async def _reconsultar(self) -> None:
        try:
            await asyncio.sleep(ATRASO_APOS_LIGAR_S)
            await self._consultar()
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            # This runs outside any poll, so an exception here would be an error nobody
            # ever sees and a task that dies in silence.
            log.debug("%s: the requery after a power on failed: %s", self.cadastro.identidade, erro)

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    async def _mandar(self, codigo: str, parametro: str) -> None:
        """One frame on the connection, opening it first when it is not up."""
        mensagem = f"{codigo}{parametro}"
        # The last gate before a socket, so nothing this file composed by mistake, and
        # nothing a list of the registration carried, can write a second frame.
        if not _NO_FIO.fullmatch(mensagem):
            raise _Falha(INVALID_VALUE)
        async with self._trava:
            if not self.conectado():
                await self._conectar()
            escritor = self._escritor
            if escritor is None:
                raise _Falha(EQ_OFFLINE)
            # A query of the poll is a code with QSTN, written once; a command is
            # written always, because it is what a diagnosis reads.
            self._transcricao().enviado(mensagem, rotina=parametro == PERGUNTA)
            try:
                escritor.write(quadro(UNIDADE_RECEIVER, mensagem.encode("ascii")))
                await escritor.drain()
            except (OSError, RuntimeError) as erro:
                self._transcricao().falhou(mensagem, erro)
                raise _Falha(EQ_OFFLINE) from erro

    async def _conectar(self) -> None:
        """The interview over UDP, then the connection on the TCP port it named."""
        await self._desconectar()
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        porta = await self._porta_de_controle(endereco)
        try:
            async with asyncio.timeout(TEMPO_LIMITE_S):
                leitor, escritor = await asyncio.open_connection(endereco, porta)
        except (OSError, TimeoutError) as erro:
            raise _Falha(EQ_OFFLINE) from erro
        self._escritor = escritor
        self._ouvinte = asyncio.create_task(self._ouvir(leitor, escritor))

    async def _porta_de_controle(self, endereco: str) -> int:
        """The control port is the one the answer of the interview carries, and
        the published number is only what a silent unit falls back to.

        The interview is asked again on EVERY connection, not once per session. A lease
        moves an address to another receiver, this long connection dies, and a driver that
        redialled a remembered port would command a stranger under the name of this
        registration, with identidade_do_aparelho still answering the MAC of the old unit.
        Two datagrams per reconnection is what costs to key by identity.
        """
        entrevista = await entrevistar(endereco)
        if entrevista is None:
            # Silence is not evidence of anybody: the last known identity is kept as it was.
            log.debug("%s: no answer to the interview", self.cadastro.identidade)
            return PORTA_PADRAO
        self._conferir_identidade(_mac_de(entrevista.identidade))
        return entrevista.porta

    def _conferir_identidade(self, identidade: str | None) -> None:
        """The identity is the key and the address is only where it answered today.

        A unit that answers another MAC at this address is another equipment, and the
        next command would reach it under the name of this registration. That is not a lost
        poll, it is a verdict, so the equipment goes offline from this instant.
        """
        if identidade is None:
            return
        conhecida = self._identidade or _mac_de(self.cadastro.identidade)
        if conhecida is not None and identidade != conhecida:
            log.warning("%s: the address now answers %s", self.cadastro.identidade, identidade)
            self._falhas = FALHAS_ATE_OFFLINE
            raise _Falha(EQ_OFFLINE)
        self._identidade = identidade

    async def _ouvir(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        """Reads the connection forever: the answers of a poll and what nobody asked for."""
        try:
            while True:
                lido = await _ler_quadro(leitor)
                if lido is None:
                    continue
                codigo, parametro = lido
                self._lidos += 1
                self._transcricao().recebido(f"{codigo}{parametro}", rotina=True)
                self._aplicar(codigo, parametro)
                if codigo == self._aguardado:
                    self._chegou.set()
        except asyncio.CancelledError:
            raise
        except _Falha as erro:
            # This runs in a task of its own, so the code cannot be returned to anybody;
            # it waits here for the poll that finds the connection gone and no frame read, and
            # is what puts erro_aparelho in the detail instead of a plain offline.
            self._quebrado = erro.codigo
            log.debug("%s: the connection ended: %s", self.cadastro.identidade, erro)
        except (OSError, asyncio.IncompleteReadError, struct.error) as erro:
            log.debug("%s: the connection ended: %s", self.cadastro.identidade, erro)
        finally:
            with suppress(OSError):
                escritor.close()

    async def _desconectar(self) -> None:
        tarefa, self._ouvinte = self._ouvinte, None
        escritor, self._escritor = self._escritor, None
        if tarefa is not None and tarefa is not asyncio.current_task():
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa
        if escritor is not None:
            with suppress(OSError):
                escritor.close()
            with suppress(OSError, TimeoutError, asyncio.CancelledError):
                await escritor.wait_closed()

    def _aplicar(self, codigo: str, parametro: str) -> None:
        """One frame of the receiver written into the state.

        A field is written only when the parameter converted. A frame this driver cannot
        read is a log and never a None written over a fact the receiver never denied: a
        malformed power frame that erased a known True would take from the panel and from DP
        101 something nobody said had changed.
        """
        if parametro == NAO_DISPONIVEL:
            # The receiver supports this and has no value for it right now, which is not a
            # number and is not a fault either.
            return
        if codigo == CODIGO_ENERGIA:
            self._escreva("ligado", _bandeira(parametro), codigo, parametro)
        elif codigo == CODIGO_MUDO:
            self._escreva("mudo", _bandeira(parametro), codigo, parametro)
        elif codigo == CODIGO_VOLUME:
            self._escreva("volume", _do_aparelho(parametro, self._escala), codigo, parametro)
        elif codigo == CODIGO_FONTE:
            lido = _valor_lido(parametro)
            if lido is None:
                log.debug("%s: %s%r says nothing", self.cadastro.identidade, codigo, parametro)
            else:
                self._trocou_de_fonte(self._do_cadastro("entradas", lido))
        elif codigo == CODIGO_MODO:
            lido = _valor_lido(parametro)
            self._escreva("modo", self._do_cadastro("modos", lido), codigo, parametro)
        elif codigo == CODIGO_TRANSPORTE:
            self._leu_transporte(parametro)
        elif codigo == CODIGO_TITULO:
            self._leu_titulo(parametro)
        # Every other code is read and dropped on purpose: the temperature frame is the sensor
        # of the receiver and not a setpoint, the audio and video ones do not fit the state of
        # and the active preset has no field there, which is not one to invent.

    def _escreva(self, campo: str, valor: object, codigo: str, parametro: str) -> None:
        """Writes a field only when the parameter of the frame converted into one."""
        if valor is None:
            log.debug("%s: %s%r says nothing", self.cadastro.identidade, codigo, parametro)
            return
        self._defina(**{campo: valor})

    def _do_cadastro(self, lista: str, valor: str | None) -> str | None:
        """The value as the list of the registration spells it,.

        The wire of this receiver is upper case hexadecimal, and matches the
        value of the driver against the item of the registration by plain equality; a list
        saved in lower case would command the receiver and never light the input back on the
        panel of the app, because "2b" and "2B" are two strings. The wire is written in upper
        case and the state carries the spelling the list carries, which is one decision.
        """
        if valor is None:
            return None
        for item in self.cadastro.listas.get(lista, ()):
            if isinstance(item.valor, str) and item.valor.strip().upper() == valor:
                return item.valor
        return valor

    def _de_rede(self, fonte: str | None) -> bool:
        """Whether this input is one the network player of the registration drives."""
        return bool(fonte) and fonte.strip().upper() in self._entradas_de_rede

    def _trocou_de_fonte(self, fonte: str) -> None:
        """The input decides whether the transport and the title mean anything at all."""
        de_rede = self._de_rede(fonte)
        estado = self.estado()
        self._defina(
            fonte=fonte or None,
            reproduzindo=estado.reproduzindo if de_rede else None,
            tocando=estado.tocando if de_rede else None,
        )

    def _leu_transporte(self, parametro: str) -> None:
        if not parametro or not self._de_rede(self.estado().fonte):
            return
        self._defina(reproduzindo=parametro[0] == TOCANDO)

    def _leu_titulo(self, parametro: str) -> None:
        if not self._de_rede(self.estado().fonte):
            return
        self._defina(tocando=_limpo(parametro) or None)

    async def _acordar(self) -> None:
        """The magic packet of wake on LAN, sent to the address of the registration.

        It goes to the receiver and not to the broadcast of the segment, because this hub
        talks to the equipment somebody registered and to nobody else,. It only
        wakes a receiver the segment still knows, and Network Standby is the reliable door.
        """
        endereco = ip_literal(self.cadastro.ip)
        magico = _magico(self._identidade or self.cadastro.identidade)
        if endereco is None or magico is None:
            return
        laco = asyncio.get_running_loop()
        try:
            transporte, _protocolo = await laco.create_datagram_endpoint(
                asyncio.DatagramProtocol, remote_addr=(endereco, PORTA_WOL)
            )
        except OSError as erro:
            log.debug("%s: the wake packet did not leave: %s", self.cadastro.identidade, erro)
            return
        try:
            transporte.sendto(magico)
        finally:
            transporte.close()

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline.

        The learned identity is NOT dropped here. It is the only yardstick that catches a
        lease which handed this address to another receiver while this one was away, and the
        interview of the next connection is judged against it.
        """
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, detalhe=codigo)


def quadro(unidade: bytes, mensagem: bytes) -> bytes:
    """One eISCP frame: the binary header, then "!", the unit, the message and CRLF."""
    dado = INICIO + unidade + mensagem + FIM_ENVIO
    return CABECALHO.pack(MAGICO, TAMANHO_CABECALHO, len(dado), VERSAO, RESERVADO) + dado


async def entrevistar(endereco: str) -> Entrevista | None:
    """The discovery question over UDP, which answers the control port and the MAC.

    The question goes out twice, as the unit of the discovery and as the one of the
    sister brand, because a unit of that brand answers only the second and is lost otherwise.
    """
    laco = asyncio.get_running_loop()
    futuro: asyncio.Future = laco.create_future()
    try:
        transporte, _protocolo = await laco.create_datagram_endpoint(
            lambda: _ProtocoloEcn(futuro), remote_addr=(endereco, PORTA_PADRAO)
        )
    except OSError as erro:
        log.debug("the interview of %s did not leave: %s", endereco, erro)
        return None
    try:
        for unidade in (UNIDADE_DESCOBERTA, UNIDADE_PIONEER):
            transporte.sendto(quadro(unidade, PERGUNTA_ECN))
        async with asyncio.timeout(PRAZO_UDP_S):
            return await futuro
    except (TimeoutError, OSError):
        return None
    finally:
        transporte.close()


class _ProtocoloEcn(asyncio.DatagramProtocol):
    """Holds the first datagram that reads as a discovery answer, and nothing else."""

    def __init__(self, futuro: asyncio.Future) -> None:
        self._futuro = futuro

    def datagram_received(self, data: bytes, addr: object) -> None:
        if self._futuro.done() or len(data) > DATAGRAMA_MAXIMO:
            return
        lido = _do_datagrama(data)
        if lido is None or lido[0] != CODIGO_DESCOBERTA:
            return
        entrevista = _entrevista_de(lido[1])
        if entrevista is not None:
            self._futuro.set_result(entrevista)

    def error_received(self, exc: Exception) -> None:
        # A receiver that is not there answers with an ICMP error, and waiting out the
        # whole deadline for it would stretch every poll of an equipment that is off.
        if not self._futuro.done():
            self._futuro.set_result(None)


def _entrevista_de(parametro: str) -> Entrevista | None:
    """Model/port/area/MAC, where the MAC is absent on an older firmware."""
    achado = _ECN.search(parametro)
    if achado is None:
        return None
    porta = int(achado.group("porta"))
    if not 1 <= porta <= 65535:
        return None
    return Entrevista(
        modelo=achado.group("modelo").strip()[:MODELO_MAXIMO],
        porta=porta,
        identidade=(achado.group("identidade") or ""),
    )


async def _ler_quadro(leitor: asyncio.StreamReader) -> tuple[str, str] | None:
    """One frame of the stream, whose length comes from the header and never from a line."""
    magico, tamanho_cabecalho, tamanho_dado, _versao, _reservado = CABECALHO.unpack(
        await leitor.readexactly(TAMANHO_CABECALHO)
    )
    if magico != MAGICO or not TAMANHO_CABECALHO <= tamanho_cabecalho <= CABECALHO_MAXIMO:
        # The frame carries the boundary of the next one, so a header that is not one
        # leaves the stream with no place to resume from and the connection is dropped.
        raise _Falha(ERRO_APARELHO)
    if tamanho_dado > DADO_MAXIMO:
        raise _Falha(ERRO_APARELHO)
    if tamanho_cabecalho > TAMANHO_CABECALHO:
        await leitor.readexactly(tamanho_cabecalho - TAMANHO_CABECALHO)
    return _mensagem(await leitor.readexactly(tamanho_dado))


def _do_datagrama(bruto: bytes) -> tuple[str, str] | None:
    """The same frame arriving whole inside one datagram of the discovery."""
    if len(bruto) < TAMANHO_CABECALHO:
        return None
    magico, tamanho_cabecalho, tamanho_dado, _versao, _reservado = CABECALHO.unpack(
        bruto[:TAMANHO_CABECALHO]
    )
    if magico != MAGICO or not TAMANHO_CABECALHO <= tamanho_cabecalho <= CABECALHO_MAXIMO:
        return None
    dado = bruto[tamanho_cabecalho : tamanho_cabecalho + tamanho_dado]
    if len(dado) != tamanho_dado:
        return None
    return _mensagem(dado)


def _mensagem(dado: bytes) -> tuple[str, str] | None:
    """The three letters of the code and the parameter, with any of the terminators cut."""
    if len(dado) < TAMANHO_MINIMO or dado[:1] != INICIO:
        return None
    corpo = dado[2:].rstrip(FIM_LEITURA)
    if len(corpo) < TAMANHO_CODIGO:
        return None
    codigo = corpo[:TAMANHO_CODIGO].decode("ascii", errors="replace")
    return codigo, corpo[TAMANHO_CODIGO:].decode("utf-8", errors="replace")


def escala_de(campos: dict[str, str]) -> int:
    """The scale of the registration, or the published default when it says nothing usable."""
    bruto = str(campos.get(CAMPO_ESCALA, "")).strip()
    if _NUMERO.fullmatch(bruto) and int(bruto) in ESCALAS:
        return int(bruto)
    return ESCALA_PADRAO


def entradas_de_rede_de(campos: dict[str, str]) -> frozenset[str]:
    """The codes of the registration whose input the network player drives,.

    The transport frame of this receiver answers stop on every input that is not one of
    these, so a code this file guessed would claim a receiver plays on an input that has no
    player, which is the defect the speakers already paid for. An empty field is
    a receiver with no network board, and reproduzindo simply never leaves None there.
    """
    bruto = campos.get(CAMPO_REDE)
    if bruto is None:
        bruto = ENTRADAS_DE_REDE_PADRAO
    codigos = (parte.strip().upper() for parte in str(bruto).split(","))
    return frozenset(codigo for codigo in codigos if _VALOR.fullmatch(codigo))


def _mac_de(identidade: str) -> str | None:
    """An identifier of the wire as an identity, or nothing at all.

    The identifier the interview carries is a string a firmware wrote, and only a whole
    MAC is an identity here. Half of one is worse than none: it keys nothing, and the magic
    packet of wake on LAN drops it in silence, which closes the only door a receiver without
    Network Standby has.
    """
    bruto = identidade.strip()
    return bruto.upper() if _MAC.fullmatch(bruto) else None


def _valor_lido(parametro: str) -> str | None:
    """The parameter of a frame as a value of a list, or nothing when it carries none."""
    return parametro.strip().upper()[:VALOR_MAXIMO] or None


def _para_o_aparelho(valor: int, escala: int) -> int:
    """The 0 to 100 as the step of the receiver, which the wire writes in hex."""
    return round(valor * escala / VOLUME_MAXIMO)


def _do_aparelho(parametro: str, escala: int) -> int | None:
    """The hexadecimal step of the receiver as the 0 to 100."""
    bruto = parametro.strip()
    if not _HEXADECIMAL.fullmatch(bruto):
        return None
    convertido = round(int(bruto, 16) * VOLUME_MAXIMO / escala)
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, convertido))


def _bandeira(parametro: str) -> bool | None:
    lido = parametro.strip()[:2]
    if lido == LIGADO:
        return True
    if lido == DESLIGADO:
        return False
    return None


def _limpo(texto: str) -> str:
    """A title of the receiver, which is text of a track and reaches the panel and the bus."""
    return _CONTROLE.sub("", texto).strip()[:TITULO_MAXIMO]


def _magico(identidade: str | None) -> bytes | None:
    """The magic packet of the MAC: six bytes of ones and the address sixteen times."""
    bruto = "" if identidade is None else identidade.replace(":", "").replace("-", "")
    if not _MAC.fullmatch(bruto):
        return None
    return b"\xff" * 6 + bytes.fromhex(bruto) * 16
