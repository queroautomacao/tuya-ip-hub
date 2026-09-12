# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Sony receivers and soundbars over the audio control API of the device itself.

What the protocol is, so nobody has to read it again:

- One POST of JSON to http://<address>:10000/sony/<service>, no password of any kind. The
  body is a call: a method, a list of parameters, an id and a VERSION of that method.
- The answer is either a result or an error, and the error is a pair of a number and a
  sentence. Two of those numbers mean the device has that method under another version, so
  the call is made again with the other one and the version that worked is remembered.
- The volume comes with the range of the model in it: the device answers its own minimum and
  maximum, so nothing about the scale has to be typed into the registration.
- An input is a URI of the device, `extInput:hdmi?port=1`, and it comes with the name the
  owner gave it, so what travels is that name and the URI is what goes on the wire.
- This is NOT the driver of a Sony television: a television speaks a different API on a
  different port, and this one is the receivers and the soundbars.
"""

import json
import logging
import re

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import Cadastro, ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.sony_avr")

TIPO = "receiver_sony"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA_PADRAO = 10000
CAMINHO = "/sony/{servico}"
TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

CAMPO_PORTA = "porta"

SISTEMA = "system"
AUDIO = "audio"
CONTEUDO = "avContent"

# The versions a method of this API can carry. A model answers one of them and refuses the
# other, and which one it is changes between models, so both are tried and the one that
# answered is kept.
VERSOES = ("1.1", "1.0")
# The device says "no such method" or "unsupported version" with these, and both mean the
# same thing here: ask again with the other version.
ERROS_DE_VERSAO = (12, 14, 40)

ALVO_DO_VOLUME = "speaker"
LIGADO = "active"
DESLIGADO = "off"
MUDO_LIGADO = "on"
MUDO_DESLIGADO = "off"

_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]{0,31}:[A-Za-z0-9_?=&/%.-]{1,120}$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"

TEXTOS = {
    "en": {
        "descricao": (
            "Sony receiver or soundbar over the audio control API of the device, with no "
            "cloud and no account. The inputs and the range of the volume are read from the "
            "device itself."
        ),
        "preparo": (
            "On the device: Network Settings, and leave Remote Start or Network Standby on, "
            "or the device stops answering the network when it is off. This is not the "
            "driver of a Sony television, which speaks another API."
        ),
        "campo_porta": "The port of the API, 10000 on almost every model.",
        "cap_volume": "Converted from the range the device answers as its own.",
    },
    "pt": {
        "descricao": (
            "Receiver ou soundbar Sony pela API de controle de áudio do próprio aparelho, sem "
            "nuvem e sem conta. As entradas e a faixa de volume são lidas do próprio aparelho."
        ),
        "preparo": (
            "No aparelho: Configurações de Rede, e deixe o Remote Start ou o Network Standby "
            "ligado, senão o aparelho para de responder à rede quando está desligado. Este não "
            "é o driver de uma televisão Sony, que fala outra API."
        ),
        "campo_porta": "A porta da API, 10000 em quase todo modelo.",
        "cap_volume": "Convertido da faixa que o próprio aparelho responde.",
    },
}

SUGESTOES = (
    Sugestao("entradas", "HDMI 1", "extInput:hdmi?port=1"),
    Sugestao("entradas", "HDMI 2", "extInput:hdmi?port=2"),
    Sugestao("entradas", "TV", "extInput:tv"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the device."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class SonyAvr(Driver):
    """One Sony receiver or soundbar, read and commanded over its own API."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Sony", "en": "Sony receiver"},
        categoria="receiver",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_VOLUME, ACAO_MUDO, ACAO_FONTE),
        config_campos=(Campo(nome=CAMPO_PORTA, tipo=TipoCampo.INTEIRO, padrao=str(PORTA_PADRAO)),),
        # The Sony television driver claims the announcement of a Sony, and a receiver that
        # answers this API is found by the range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Sony",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._entradas: dict[str, str] = {}
        self._versoes: dict[str, str] = {}
        self._faixa = (0, 100)
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
        if acao in (ACAO_LIGAR, ACAO_DESLIGAR):
            ligar = acao == ACAO_LIGAR
            await self._chamar(
                SISTEMA, "setPowerStatus", [{"status": LIGADO if ligar else DESLIGADO}]
            )
            self._defina(ligado=ligar)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._chamar(
                AUDIO,
                "setAudioVolume",
                [{"target": ALVO_DO_VOLUME, "volume": str(self._para_o_aparelho(valor))}],
            )
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._chamar(
                AUDIO, "setAudioMute", [{"mute": MUDO_LIGADO if valor else MUDO_DESLIGADO}]
            )
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            uri = self._uri_da_entrada(valor)
            if uri is None:
                return INVALID_VALUE
            await self._chamar(CONTEUDO, "setPlayContent", [{"uri": uri}])
            self._defina(fonte=self._entradas.get(uri, uri))
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: power, volume and the input in front, plus the inputs once."""
        energia = await self._chamar(SISTEMA, "getPowerStatus", [])
        volume = await self._chamar(AUDIO, "getVolumeInformation", [{}])
        if not self._entradas:
            await self._ler_entradas()
        ligado = _texto(_primeiro(energia), "status") == LIGADO
        alto_falante = _do_alvo(volume, ALVO_DO_VOLUME)
        nivel = _inteiro(alto_falante, "volume")
        minimo = _inteiro(alto_falante, "minVolume", 0)
        maximo = _inteiro(alto_falante, "maxVolume", 100)
        if minimo is not None and maximo is not None and minimo < maximo:
            self._faixa = (minimo, maximo)
        mudo = _texto(alto_falante, "mute")
        atual = await self._entrada_em_frente()
        self._defina(
            online=True,
            ligado=ligado,
            volume=self._do_aparelho(nivel),
            mudo=None if not mudo else mudo == MUDO_LIGADO,
            fonte=self._entradas.get(atual, atual) or None,
            fontes=tuple(self._entradas.values()),
            detalhe="",
        )

    async def _ler_entradas(self) -> None:
        """The inputs of the device and the name the owner gave each one."""
        try:
            terminais = await self._chamar(CONTEUDO, "getCurrentExternalTerminalsStatus", [])
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                raise
            log.debug("%s: the device did not list its inputs", self._id())
            return
        achadas: dict[str, str] = {}
        for terminal in _lista(terminais):
            uri = _texto(terminal, "uri")
            if uri:
                achadas[uri] = _texto(terminal, "title") or uri
        if achadas:
            self._entradas = achadas
            log.info("%s: the device has inputs %s", self._id(), sorted(achadas.items()))

    async def _entrada_em_frente(self) -> str:
        """Which of the terminals of the device is the active one, when it says so."""
        try:
            terminais = await self._chamar(CONTEUDO, "getCurrentExternalTerminalsStatus", [])
        except _Falha:
            return ""
        for terminal in _lista(terminais):
            if _texto(terminal, "active") == LIGADO:
                return _texto(terminal, "uri")
        return ""

    async def _chamar(self, servico: str, metodo: str, parametros: list) -> object:
        """One call, made again with the other version when the device asks for it."""
        preferida = self._versoes.get(metodo)
        tentativas = (preferida,) if preferida else VERSOES
        ultimo: tuple[int, str] | None = None
        for versao in tentativas:
            resultado, erro = await self._uma_chamada(servico, metodo, parametros, versao)
            if erro is None:
                self._versoes[metodo] = versao
                return resultado
            if erro[0] not in ERROS_DE_VERSAO:
                self._fio.recusado(f"{servico}.{metodo}", INVALID_VALUE)
                raise _Falha(INVALID_VALUE)
            ultimo = erro
        log.warning("%s: the device refused %s: %s", self._id(), metodo, ultimo)
        raise _Falha(ERRO_APARELHO)

    async def _uma_chamada(
        self, servico: str, metodo: str, parametros: list, versao: str
    ) -> tuple[object, tuple[int, str] | None]:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        url = f"http://{endereco}:{self._porta()}{CAMINHO.format(servico=servico)}"
        pedido = {"method": metodo, "params": parametros, "id": 1, "version": versao}
        rotina = metodo.startswith("get")
        self._fio.enviado(f"{servico}.{metodo} {json.dumps(parametros)}", rotina=rotina)
        sessao = await self._abrir()
        try:
            async with sessao.post(url, json=pedido, allow_redirects=False) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(f"{servico}.{metodo}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado != 200:
            raise _Falha(ERRO_APARELHO)
        try:
            documento = json.loads(bruto.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError) as erro:
            raise _Falha(ERRO_APARELHO) from erro
        if not isinstance(documento, dict):
            raise _Falha(ERRO_APARELHO)
        erro_lido = documento.get("error")
        if isinstance(erro_lido, list) and erro_lido:
            numero = erro_lido[0] if isinstance(erro_lido[0], int) else 0
            frase = str(erro_lido[1]) if len(erro_lido) > 1 else ""
            return None, (numero, frase)
        return documento.get("result"), None

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._entradas = {}
        self._defina(online=False, detalhe=codigo)

    def _porta(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_PORTA, "").strip()
        if not bruto.isdigit():
            return PORTA_PADRAO
        porta = int(bruto)
        return porta if 1 <= porta <= 65535 else PORTA_PADRAO

    def _do_aparelho(self, nivel: int | None) -> int | None:
        if nivel is None:
            return None
        minimo, maximo = self._faixa
        return max(0, min(100, round((nivel - minimo) * 100 / (maximo - minimo))))

    def _para_o_aparelho(self, valor: int) -> int:
        minimo, maximo = self._faixa
        return round(minimo + valor * (maximo - minimo) / 100)

    def _uri_da_entrada(self, valor: object) -> str | None:
        """The URI of the input, from the name the owner gave it or from the URI itself."""
        if not isinstance(valor, str):
            return None
        limpo = valor.strip()
        for uri, nome in self._entradas.items():
            if nome.casefold() == limpo.casefold():
                return uri
        return limpo if _URI.match(limpo) else None

    def _id(self) -> str:
        return self.cadastro.identidade

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The MAC of the device, which it answers with no pairing and keeps for good."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        url = f"http://{endereco}:{PORTA_PADRAO}{CAMINHO.format(servico=SISTEMA)}"
        for versao in VERSOES:
            pedido = {
                "method": "getSystemInformation",
                "params": [],
                "id": 1,
                "version": versao,
            }
            try:
                async with (
                    ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao,
                    sessao.post(url, json=pedido, allow_redirects=False) as resposta,
                ):
                    if resposta.status != 200:
                        return None
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
            except (TimeoutError, ClientError, OSError, ValueError):
                return None
            try:
                documento = json.loads(bruto.decode("utf-8", errors="replace"))
            except (ValueError, RecursionError):
                return None
            if not isinstance(documento, dict) or "error" in documento:
                continue
            mac = _texto(_primeiro(documento.get("result")), "macAddr")
            if mac:
                return mac.strip().lower()
        return None


def _lista(resultado: object) -> list:
    """The list inside a result, which the API wraps in a list of its own."""
    if not isinstance(resultado, list):
        return []
    if len(resultado) == 1 and isinstance(resultado[0], list):
        return [item for item in resultado[0] if isinstance(item, dict)]
    return [item for item in resultado if isinstance(item, dict)]


def _primeiro(resultado: object) -> dict:
    itens = _lista(resultado)
    return itens[0] if itens else {}


def _do_alvo(resultado: object, alvo: str) -> dict:
    """The volume of THAT target, or the first one when the device names none."""
    itens = _lista(resultado)
    for item in itens:
        if _texto(item, "target") == alvo:
            return item
    return itens[0] if itens else {}


def _texto(documento: dict, chave: str) -> str:
    valor = documento.get(chave)
    return valor.strip() if isinstance(valor, str) else ""


def _inteiro(documento: dict, chave: str, padrao: int | None = None) -> int | None:
    valor = documento.get(chave)
    if isinstance(valor, bool):
        return padrao
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.strip().lstrip("-").isdigit():
        return int(valor)
    return padrao
