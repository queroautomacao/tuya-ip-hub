# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Denon and Marantz receiver over the HTTP interface of the AVR,.

 paid for the first decision of this file: a Denon accepts ONE telnet connection at
a time and fights with any other controller that wants it, so this driver never opens one.
Everything goes over HTTP, which the receiver answers to as many clients as ask, and which is
the same door the denonavr library of Home Assistant uses when it is not on telnet.

What the receiver does:

- a command is a GET on /goform/formiPhoneAppDirect.xml with the command of the IP chart of
Denon in the query string, verbatim: PWON, MV50, MUON, SIDVD, MSMOVIE;
- the state is one GET on /goform/formMainZone_MainZoneXmlStatusLite.xml, an XML of five
scalars, read here by pattern and not by an XML parser: five fixed fields do not justify
handing a document from a device on the LAN to a parser with entities;
- the volume of the chart is 00 to 98 and the volume of the status is dB from -80.0, so the
0 to 100 is converted in both directions in one place;
- the port is 8080 on the AVR-X of 2016 and later and 80 on the older ones, and no field of
the registration can tell them apart, so the driver tries the other one when the first
answers nothing and remembers which one worked.
"""

import logging
import re

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Descoberta, Manifesto, Sugestao

log = logging.getLogger("iphub.drivers.nativos.denon")

TIPO = "receiver_denon"

# The AVR-X of 2016 and later answers on 8080 and the older ones on 80; nothing in a
# registration tells them apart, so both are tried and the one that answered is kept.
PORTAS = (8080, 80)

CAMINHO_COMANDO = "/goform/formiPhoneAppDirect.xml?{comando}"
CAMINHO_ESTADO = "/goform/formMainZone_MainZoneXmlStatusLite.xml"
CAMINHO_APARELHO = "/goform/Deviceinfo.xml"

# The commands of the IP chart of Denon this driver writes.
MANDA_LIGAR = "PWON"
MANDA_DESLIGAR = "PWSTANDBY"
MANDA_VOLUME = "MV{valor:02d}"
MANDA_MUDO = "MUON"
MANDA_SOM = "MUOFF"
MANDA_FONTE = "SI{valor}"
MANDA_MODO = "MS{valor}"

# The chart takes 00 to 98 for the volume, where 80 is the 0 dB of the display; the
# contract is 0 to 100 and the conversion lives here and nowhere else.
VOLUME_DO_APARELHO = 98
VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
# The status answers dB, and the bottom of the scale of these receivers is -80.0 dB.
DB_MINIMO = -80.0

LIGADO = "ON"
MUDO_LIGADO = "on"


# A receiver on the LAN answers this XML, and five fixed fields are read by pattern
# instead of by a parser that would also read entities somebody wrote into the document.
def _campo(nome: str) -> re.Pattern[str]:
    return re.compile(rf"<{nome}>\s*<value>(.*?)</value>", re.IGNORECASE | re.DOTALL)


CAMPO_ENERGIA = _campo("Power")
CAMPO_FONTE = _campo("InputFuncSelect")
CAMPO_VOLUME = _campo("MasterVolume")
CAMPO_MUDO = _campo("Mute")
CAMPO_MODO = _campo("SurrMode")
CAMPO_MAC = _campo("MacAddress")

# The value lands in the query string of the receiver, so anything that is not one of
# these bytes could close the command and write a second parameter nobody wrote here.
_NO_FIO = re.compile(r"[A-Za-z0-9/.: _-]{1,32}")
_MAC = re.compile(r"[0-9A-Fa-f]{12}")

TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 128 * 1024
FALHAS_ATE_OFFLINE = 2
TEXTO_MAXIMO = 200

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_MODO = "modo"
ACAO_ATALHO = "atalho"
ACAO_EXTRA = "comando_extra"

# The inputs and the sound modes of a receiver are words of the chart of Denon that
# nobody memorises, so the driver offers the usual ones and the integrator renames them.
SUGESTOES = (
    Sugestao("entradas", "Blu-ray", "BD"),
    Sugestao("entradas", "TV", "TV"),
    Sugestao("entradas", "Media Player", "MPLAY"),
    Sugestao("entradas", "Game", "GAME"),
    Sugestao("entradas", "CD", "CD"),
    Sugestao("entradas", "Tuner", "TUNER"),
    Sugestao("entradas", "Bluetooth", "BT"),
    Sugestao("entradas", "Rede", "NET"),
    Sugestao("modos", "Filme", "MOVIE"),
    Sugestao("modos", "Musica", "MUSIC"),
    Sugestao("modos", "Jogo", "GAME"),
    Sugestao("modos", "Direto", "DIRECT"),
    Sugestao("modos", "Estereo", "STEREO"),
    Sugestao("modos", "Auto", "AUTO"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Denon and Marantz receiver over the HTTP interface of the AVR. It never opens a "
            "telnet connection: the receiver accepts one at a time and fights with any other "
            "controller that wants it."
        ),
        "preparo": (
            "Set Network Control to Always On (Setup, Network, Network Control), or the receiver "
            "stops answering in standby and cannot be turned on from here. Reserve its address on "
            "the router. No password."
        ),
        "cap_fonte": (
            "The value is the word of the chart of Denon for the input, with no SI in front: "
            "BD, TV, MPLAY, GAME, CD, TUNER, BT, NET."
        ),
        "cap_modo": (
            "The value is the word of the chart of Denon for the sound mode, with no MS in "
            "front: MOVIE, MUSIC, GAME, DIRECT, STEREO, AUTO."
        ),
        "cap_atalho": (
            "A shortcut is any command of the IP chart of Denon, written whole: MSDOLBY "
            "DIGITAL, PSBAS UP, NS9A. It goes to the receiver as it is."
        ),
        "cap_comando_extra": (
            "The same as a shortcut, typed on the spot instead of registered on the list."
        ),
    },
    "pt": {
        "descricao": (
            "Receiver Denon e Marantz pela interface HTTP do AVR. Ele nunca abre conexão "
            "telnet: o receiver aceita uma por vez e briga com qualquer outro controlador que "
            "a queira."
        ),
        "preparo": (
            "Ponha o Controle de rede em Sempre ligado (Configuração, Rede, Controle de rede), ou "
            "o receiver para de responder em standby e não liga daqui. Reserve o endereço dele no "
            "roteador. Sem senha."
        ),
        "cap_fonte": (
            "O valor é a palavra da tabela da Denon para a entrada, sem o SI na frente: BD, "
            "TV, MPLAY, GAME, CD, TUNER, BT, NET."
        ),
        "cap_modo": (
            "O valor é a palavra da tabela da Denon para o modo de som, sem o MS na frente: "
            "MOVIE, MUSIC, GAME, DIRECT, STEREO, AUTO."
        ),
        "cap_atalho": (
            "Um atalho é qualquer comando da tabela IP da Denon, escrito inteiro: MSDOLBY "
            "DIGITAL, PSBAS UP, NS9A. Ele vai para o receiver como está."
        ),
        "cap_comando_extra": (
            "O mesmo que um atalho, digitado na hora em vez de cadastrado na lista."
        ),
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the receiver."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Denon(Driver):
    """One Denon or Marantz receiver, read and commanded over HTTP."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Denon / Marantz", "en": "Denon / Marantz receiver"},
        categoria="receiver",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_MODO,
            ACAO_ATALHO,
            ACAO_EXTRA,
        ),
        descoberta=Descoberta(ssdp_fabricantes=("denon", "marantz")),
        textos=TEXTOS,
        marca="Denon",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._falhas = 0
        # Two ports and no field that tells them apart, so the one that answered is kept
        # and the other is only tried again after the receiver goes quiet.
        self._porta: int | None = None

    async def parar(self) -> None:
        sessao = self._sessao
        self._sessao = None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The MAC the receiver answers, which is its identity on this hub."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        for porta in PORTAS:
            corpo_lido = await _buscar(f"http://{_hospedeiro(endereco)}:{porta}{CAMINHO_APARELHO}")
            if corpo_lido is None:
                continue
            mac = _texto(CAMPO_MAC, corpo_lido).replace(":", "").replace("-", "")
            if _MAC.fullmatch(mac):
                return mac.upper()
        return None

    async def atualizar(self) -> None:
        try:
            lido = await self._pedir(CAMINHO_ESTADO)
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        self._aplicar(lido)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            await self._mandar(MANDA_LIGAR)
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._mandar(MANDA_DESLIGAR)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._com_prefixo(MANDA_FONTE, valor, fonte=True)
        if acao == ACAO_MODO:
            return await self._com_prefixo(MANDA_MODO, valor, modo=True)
        if acao in (ACAO_ATALHO, ACAO_EXTRA):
            return await self._crua(valor)
        return await super().executar(acao, valor)

    async def _trocar_volume(self, valor: object) -> str | None:
        if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
            return INVALID_VALUE
        await self._mandar(MANDA_VOLUME.format(valor=_para_o_aparelho(valor)))
        self._defina(volume=valor)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        await self._mandar(MANDA_MUDO if valor else MANDA_SOM)
        self._defina(mudo=valor)
        return None

    async def _com_prefixo(
        self, modelo: str, valor: object, *, fonte: bool = False, modo: bool = False
    ) -> str | None:
        """One word of the chart of Denon behind its prefix, SI for an input and MS for a mode."""
        if not isinstance(valor, str) or not _NO_FIO.fullmatch(valor.strip()):
            return INVALID_VALUE
        palavra = valor.strip().upper()
        await self._mandar(modelo.format(valor=palavra))
        if fonte:
            self._defina(fonte=palavra)
        if modo:
            self._defina(modo=palavra)
        return None

    async def _crua(self, valor: object) -> str | None:
        """Any command of the IP chart, written whole and sent as it is."""
        if not isinstance(valor, str) or not _NO_FIO.fullmatch(valor.strip()):
            return INVALID_VALUE
        await self._mandar(valor.strip())
        return None

    def _aplicar(self, lido: str) -> None:
        energia = _texto(CAMPO_ENERGIA, lido)
        fonte = _texto(CAMPO_FONTE, lido)
        modo = _texto(CAMPO_MODO, lido)
        self._defina(
            online=True,
            ligado=None if not energia else energia.upper() == LIGADO,
            volume=_do_aparelho(_texto(CAMPO_VOLUME, lido)),
            mudo=_mudo_de(_texto(CAMPO_MUDO, lido)),
            fonte=fonte or None,
            modo=modo or None,
            detalhe="",
        )

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline; the port is tried
        again from the top, because a receiver that came back may be another model.
        """
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._porta = None
        self._defina(online=False, detalhe=codigo)

    async def _mandar(self, comando: str) -> None:
        """One command of the chart; the receiver answers an empty document to every one."""
        if not _NO_FIO.fullmatch(comando):
            raise _Falha(INVALID_VALUE)
        await self._pedir(CAMINHO_COMANDO.format(comando=_na_query(comando)))

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    async def _pedir(self, caminho: str) -> str:
        """One exchange with the receiver, on the port that answered last time."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        anfitriao = _hospedeiro(endereco)
        portas = (self._porta,) if self._porta is not None else PORTAS
        rotina = caminho == CAMINHO_ESTADO
        for porta in portas:
            self._transcricao().enviado(f"GET :{porta}{caminho}", rotina=rotina)
            lido = await _buscar(f"http://{anfitriao}:{porta}{caminho}", self._abrir)
            if lido is not None:
                self._porta = porta
                self._transcricao().recebido(lido or "200 (empty)", rotina=rotina)
                return lido
            self._transcricao().falhou(f"GET :{porta}{caminho}", "no answer")
        raise _Falha(EQ_OFFLINE)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao


async def _buscar(url: str, abrir=None) -> str | None:
    """The body of one GET, or None when the receiver did not answer it."""
    fechar = abrir is None
    sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) if fechar else await abrir()
    try:
        async with sessao.get(
            url,
            # A receiver answering a redirect would send the hub to whatever host it
            # names, which is the LAN proxy refuses.
            allow_redirects=False,
        ) as resposta:
            bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
            if resposta.status >= 400:
                log.debug("the receiver answered HTTP %d to %s", resposta.status, url)
                return None
    except (TimeoutError, ClientError, OSError, ValueError) as erro:
        log.debug("the receiver did not answer %s: %s", url, erro or type(erro).__name__)
        return None
    finally:
        if fechar and not sessao.closed:
            await sessao.close()
    return bruto.decode("utf-8", errors="replace")


def _na_query(comando: str) -> str:
    """The command as it travels in the query string: the chart uses spaces, the wire does not."""
    return comando.replace(" ", "%20")


def _hospedeiro(endereco: str) -> str:
    return f"[{endereco}]" if ":" in endereco else endereco


def _texto(padrao: re.Pattern[str], documento: str) -> str:
    achado = padrao.search(documento)
    return "" if achado is None else achado.group(1).strip()[:TEXTO_MAXIMO]


def _mudo_de(bruto: str) -> bool | None:
    lido = bruto.strip().lower()
    if lido in ("on", "true"):
        return True
    if lido in ("off", "false"):
        return False
    return None


def _para_o_aparelho(valor: int) -> int:
    """The 0 to 100 as the 00 to 98 of the chart of Denon."""
    return round(valor * VOLUME_DO_APARELHO / VOLUME_MAXIMO)


def _do_aparelho(bruto: str) -> int | None:
    """The dB the status answers as the 0 to 100, or None for a receiver that
    answered nothing readable.
    """
    lido = bruto.strip()
    if not lido:
        return None
    try:
        db = float(lido)
    except ValueError:
        return None
    # The status of these receivers answers dB from -80.0 up, and the chart writes the
    # same scale shifted by 80; the panel and the bus only ever see 0 to 100.
    convertido = (db - DB_MINIMO) * VOLUME_MAXIMO / VOLUME_DO_APARELHO
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, round(convertido)))
