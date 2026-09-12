# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Emotiva processors over the XML control of the processor itself.

What the protocol is, so nobody has to read it again:

- It is XML over UDP, not TCP: a datagram of XML goes to the control port of the processor
  and the answer comes back as another datagram. The control port is 7002 on every model of
  this line, and it is a field of the registration for the ones that were moved.
- The processor answers to the port the request CAME FROM, so one socket is bound and kept,
  and every registration on the same hub SHARES it: two sockets on the same port is what the
  operating system refuses, and a hub with three processors is three registrations.
- Asking is `<emotivaUpdate>` with one empty element per thing wanted, and the answer is the
  same shape with a value on each. Commanding is `<emotivaControl>` with the command as the
  element, which is why a command that is not one of the ones written here never travels.
- The volume is in decibels, from a floor of -96 to a ceiling of +11 on almost every model,
  and both are fields of the registration because the processor does not answer its own.
- The inputs are numbered and named apart: the answer carries one element per input with the
  name the owner gave it, and selecting one is a command of that number.
"""

import asyncio
import logging
import re
from xml.etree import ElementTree

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.emotiva")

TIPO = "processador_emotiva"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

# The port the processor listens on, and the one this hub binds so the answer has somewhere
# to land. They are the same number because the processor answers where the request came from.
PORTA_DE_CONTROLE = 7002
PORTA_LOCAL = 7002
RESPOSTA_S = 2.0
DATAGRAMA_MAXIMO = 16 * 1024
FALHAS_ATE_OFFLINE = 2

CABECALHO = '<?xml version="1.0" encoding="utf-8"?>'

CAMPO_PORTA = "porta"
CAMPO_DB_MINIMO = "db_minimo"
CAMPO_DB_MAXIMO = "db_maximo"

DB_MINIMO_PADRAO = -96.0
DB_MAXIMO_PADRAO = 11.0

ENTRADAS_ATE = 8

LIGADO = "On"
DESLIGADO = "Off"
MUDO_NO_VOLUME = "Mute"

# What is asked on every poll, plus the name of each input, asked once.
DO_ESTADO = ("power", "volume", "source", "mute")
DAS_ENTRADAS = tuple(f"input_{numero}" for numero in range(1, ENTRADAS_ATE + 1))

_DTD = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_NUMERO = re.compile(r"^[+-]?[0-9]+(\.[0-9]+)?$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"

TEXTOS = {
    "en": {
        "descricao": (
            "Emotiva processor over the XML control of the processor, with no cloud and no "
            "account. It speaks UDP, so the hub keeps one socket for every processor of the "
            "installation."
        ),
        "preparo": (
            "On the processor: Setup, Network, and leave the network control enabled. The "
            "processor and the hub have to be on the same network, because the control of "
            "this line does not cross a router."
        ),
        "campo_porta": "The control port of the processor, 7002 on every model of this line.",
        "campo_db_minimo": "The floor of the volume in decibels, -96 on almost every model.",
        "campo_db_maximo": "The ceiling of the volume in decibels, 11 on almost every model.",
        "cap_volume": "Converted from the decibels of the processor.",
    },
    "pt": {
        "descricao": (
            "Processador Emotiva pelo controle XML do próprio processador, sem nuvem e sem "
            "conta. Ele fala UDP, então o hub mantém um socket para todos os processadores da "
            "instalação."
        ),
        "preparo": (
            "No processador: Setup, Network, e deixe o controle por rede habilitado. O "
            "processador e o hub precisam estar na mesma rede, porque o controle desta linha "
            "não atravessa roteador."
        ),
        "campo_porta": "A porta de controle do processador, 7002 em todo modelo desta linha.",
        "campo_db_minimo": "O piso do volume em decibéis, -96 em quase todo modelo.",
        "campo_db_maximo": "O teto do volume em decibéis, 11 em quase todo modelo.",
        "cap_volume": "Convertido dos decibéis do processador.",
    },
}

SUGESTOES = (
    Sugestao("entradas", "HDMI 1", "1"),
    Sugestao("entradas", "HDMI 2", "2"),
    Sugestao("entradas", "HDMI 3", "3"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the processor."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class _Canal(asyncio.DatagramProtocol):
    """One bound socket, shared by every registration that talks on the same port.

    The processor answers to the port the request came from, so the socket has to stay bound
    while the hub runs; and two sockets on one port is what the operating system refuses, so
    the registrations of an installation share this one and each of them waits for the
    datagram of ITS address.
    """

    _abertos: dict[int, "_Canal"] = {}

    def __init__(self) -> None:
        self._transporte: asyncio.DatagramTransport | None = None
        self._esperas: dict[str, asyncio.Future[bytes]] = {}
        self._quantos = 0

    @classmethod
    async def abrir(cls, porta: int) -> "_Canal":
        canal = cls._abertos.get(porta)
        if canal is None:
            canal = cls()
            laco = asyncio.get_running_loop()
            transporte, _protocolo = await laco.create_datagram_endpoint(
                lambda: canal, local_addr=("0.0.0.0", porta)
            )
            canal._transporte = transporte
            cls._abertos[porta] = canal
        canal._quantos += 1
        return canal

    def fechar(self) -> None:
        self._quantos -= 1
        if self._quantos > 0:
            return
        transporte, self._transporte = self._transporte, None
        for porta, canal in list(self._abertos.items()):
            if canal is self:
                del self._abertos[porta]
        if transporte is not None:
            transporte.close()

    def datagram_received(self, dados: bytes, remetente: tuple) -> None:
        espera = self._esperas.get(remetente[0])
        if espera is not None and not espera.done():
            espera.set_result(dados)

    def error_received(self, exc: Exception) -> None:
        log.debug("the socket of the processors reported %s", exc)

    async def perguntar(self, ip: str, porta: int, pedido: bytes, prazo: float) -> bytes:
        """One datagram out and the next datagram of THAT address back."""
        transporte = self._transporte
        if transporte is None:
            raise _Falha(EQ_OFFLINE)
        espera: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        self._esperas[ip] = espera
        try:
            transporte.sendto(pedido, (ip, porta))
            async with asyncio.timeout(prazo):
                return await espera
        except TimeoutError:
            raise _Falha(EQ_OFFLINE) from None
        except OSError as erro:
            raise _Falha(EQ_OFFLINE) from erro
        finally:
            self._esperas.pop(ip, None)

    def mandar(self, ip: str, porta: int, pedido: bytes) -> None:
        """One datagram the processor does not answer, which is what a command is."""
        transporte = self._transporte
        if transporte is None:
            raise _Falha(EQ_OFFLINE)
        try:
            transporte.sendto(pedido, (ip, porta))
        except OSError as erro:
            raise _Falha(EQ_OFFLINE) from erro


class Emotiva(Driver):
    """One Emotiva processor, asked and commanded in XML over UDP."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Processador Emotiva", "en": "Emotiva processor"},
        categoria="receiver",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_VOLUME, ACAO_MUDO, ACAO_FONTE),
        config_campos=(
            Campo(nome=CAMPO_PORTA, tipo=TipoCampo.INTEIRO, padrao=str(PORTA_DE_CONTROLE)),
            Campo(nome=CAMPO_DB_MINIMO, tipo=TipoCampo.TEXTO, padrao=str(DB_MINIMO_PADRAO)),
            Campo(nome=CAMPO_DB_MAXIMO, tipo=TipoCampo.TEXTO, padrao=str(DB_MAXIMO_PADRAO)),
        ),
        # The processor announces itself on a port of its own and not on anything this hub
        # sweeps, so the integrator registers it by the address the installer gave it.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Emotiva",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._canal: _Canal | None = None
        self._entradas: dict[str, str] = {}
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        canal, self._canal = self._canal, None
        if canal is not None:
            canal.fechar()

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
            await self._comandar("power_on", "0")
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._comandar("power_off", "0")
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._comandar("set_volume", self._para_db(valor))
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._comandar("mute_on" if valor else "mute_off", "0")
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            numero = self._numero_da_entrada(valor)
            if numero is None:
                return INVALID_VALUE
            await self._comandar(f"source_{numero}", "0")
            self._defina(fonte=self._entradas.get(numero, numero))
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: the state of the processor, and the names of its inputs once."""
        pedidos = DO_ESTADO if self._entradas else DO_ESTADO + DAS_ENTRADAS
        lidos = await self._perguntar(pedidos)
        self._guardar_entradas(lidos)
        volume = lidos.get("volume", "")
        mudo = lidos.get("mute", "")
        energia = lidos.get("power", "")
        # The processor writes the word Mute in the volume itself when it is muted, and then
        # there is no level to read: what it says is that the level is not audible.
        mudo_pelo_volume = volume == MUDO_NO_VOLUME
        self._defina(
            online=True,
            ligado=None if not energia else energia == LIGADO,
            volume=None if mudo_pelo_volume else self._do_db(volume),
            mudo=True if mudo_pelo_volume else (None if not mudo else mudo == LIGADO),
            fonte=lidos.get("source") or None,
            fontes=tuple(self._entradas.values()),
            detalhe="",
        )

    def _guardar_entradas(self, lidos: dict[str, str]) -> None:
        """The names the owner gave the inputs, which the processor answers one per input."""
        achadas = {
            nome[len("input_") :]: valor
            for nome, valor in lidos.items()
            if nome.startswith("input_") and valor
        }
        if achadas:
            self._entradas = achadas
            log.info("%s: the processor has inputs %s", self._id(), sorted(achadas.items()))

    async def _perguntar(self, nomes: tuple[str, ...]) -> dict[str, str]:
        canal = await self._canal_aberto()
        dentro = "".join(f"<{nome} />" for nome in nomes)
        pedido = f"{CABECALHO}<emotivaUpdate>{dentro}</emotivaUpdate>".encode()
        self._fio.enviado(" ".join(nomes), rotina=True)
        bruto = await canal.perguntar(self._endereco(), self._porta(), pedido, RESPOSTA_S)
        self._fio.recebido(bruto.decode("utf-8", errors="replace"), rotina=True)
        raiz = _arvore(bruto)
        if raiz is None:
            raise _Falha(ERRO_APARELHO)
        lidos: dict[str, str] = {}
        for elemento in raiz:
            visivel = (elemento.get("visible") or "").strip()
            if elemento.tag.startswith("input_") and visivel not in ("", "true"):
                continue
            valor = (elemento.get("value") or "").strip()
            if valor:
                lidos[elemento.tag] = valor
        if not lidos:
            raise _Falha(ERRO_APARELHO)
        return lidos

    async def _comandar(self, comando: str, valor: str) -> None:
        canal = await self._canal_aberto()
        pedido = (
            f'{CABECALHO}<emotivaControl><{comando} value="{valor}" /></emotivaControl>'
        ).encode()
        self._fio.enviado(f"{comando}={valor}")
        canal.mandar(self._endereco(), self._porta(), pedido)

    async def _canal_aberto(self) -> _Canal:
        canal = self._canal
        if canal is None:
            try:
                canal = await _Canal.abrir(PORTA_LOCAL)
            except OSError as erro:
                self._fio.falhou("bind", erro)
                raise _Falha(EQ_OFFLINE) from erro
            self._canal = canal
        return canal

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._entradas = {}
        self._defina(online=False, detalhe=codigo)

    def _endereco(self) -> str:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco

    def _porta(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_PORTA, "").strip()
        if not bruto.isdigit():
            return PORTA_DE_CONTROLE
        porta = int(bruto)
        return porta if 1 <= porta <= 65535 else PORTA_DE_CONTROLE

    def _faixa(self) -> tuple[float, float]:
        minimo = _decimal(self.cadastro.campos.get(CAMPO_DB_MINIMO, ""), DB_MINIMO_PADRAO)
        maximo = _decimal(self.cadastro.campos.get(CAMPO_DB_MAXIMO, ""), DB_MAXIMO_PADRAO)
        return (minimo, maximo) if minimo < maximo else (DB_MINIMO_PADRAO, DB_MAXIMO_PADRAO)

    def _do_db(self, bruto: str) -> int | None:
        if not _NUMERO.match(bruto):
            return None
        minimo, maximo = self._faixa()
        db = max(minimo, min(maximo, float(bruto)))
        return round((db - minimo) * 100 / (maximo - minimo))

    def _para_db(self, valor: int) -> str:
        minimo, maximo = self._faixa()
        db = minimo + valor * (maximo - minimo) / 100
        # The processor takes whole and half decibels and nothing between them.
        return f"{round(db * 2) / 2:g}"

    def _numero_da_entrada(self, valor: object) -> str | None:
        """The number of the input, from the name the owner gave it or from the number."""
        if not isinstance(valor, str):
            return None
        limpo = valor.strip()
        for numero, nome in self._entradas.items():
            if nome.casefold() == limpo.casefold():
                return numero
        if limpo.isdigit() and 1 <= int(limpo) <= ENTRADAS_ATE:
            return str(int(limpo))
        return None

    def _id(self) -> str:
        return self.cadastro.identidade


def _arvore(bruto: bytes) -> ElementTree.Element | None:
    if not bruto or _DTD.search(bruto):
        return None
    try:
        return ElementTree.fromstring(bruto)
    except (ElementTree.ParseError, ValueError):
        return None


def _decimal(bruto: str, padrao: float) -> float:
    try:
        return float(bruto.strip())
    except ValueError:
        return padrao
