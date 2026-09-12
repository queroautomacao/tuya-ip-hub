# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Philips televisions speaking Jointspace, the JSON API of the sets before Android.

What the protocol is, so nobody has to read it again:

- Plain HTTP on port 1925, JSON in and JSON out, and NO authentication at all: whoever is on
  the network commands the television. Every path carries the API version as its first
  segment, /1/ on the sets of 2011 to 2013 and /5/ on the ones of 2014 and 2015, and the two
  are the same paths behind different numbers, so this driver asks the television which one
  it answers and remembers it.
- The volume is a level with the range the television itself publishes, usually 0 to 60, and
  the mute travels in the SAME message as the level, so setting one always writes both.
- A key of the remote is a WORD, posted to /input/key, and the words are the ones of the
  protocol; keys it does not have are simply not declared here.
- The input is readable and settable by id, which is why fonte is a capability. A set that
  dropped that endpoint gets a stable code instead of a guess.
- There is no power state to read: a television in standby leaves the network, so one that
  answers is on and one that does not answer twice is offline, and turning it back on is a
  Wake on LAN packet to the MAC of the registration.

What is deliberately OUT: the API version 6 of the Android sets, which listens on 1926 with
TLS, digest authentication and a pairing. A Philips with Android is reached by the Android
television driver of this hub, which speaks the remote protocol of the platform and does far
more than this one.
"""

import asyncio
import json
import logging
import re
import socket

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import Cadastro, ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.philips")

TIPO = "tv_philips_jointspace"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA = 1925
TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

# The two versions this driver speaks, newest first, which is the order it asks in.
VERSOES = ("5", "1")

CAMPO_API = "api"
CAMPO_MAC = "mac"
PORTA_WOL = 9
_MAC = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}$")

VOLUME_MAXIMO_PADRAO = 60

# The keys of the remote, in the words of the hub and in the words of the protocol. What is
# absent is absent on the television: this API has no guide key and no exit key.
TECLAS = {
    "cima": "CursorUp",
    "baixo": "CursorDown",
    "esquerda": "CursorLeft",
    "direita": "CursorRight",
    "ok": "Confirm",
    "voltar": "Back",
    "inicio": "Home",
    "menu": "Options",
    "info": "Info",
    "mais": "VolumeUp",
    "menos": "VolumeDown",
    "canal_mais": "ChannelStepUp",
    "canal_menos": "ChannelStepDown",
    "play_pause": "PlayPause",
    "proxima": "Next",
    "anterior": "Previous",
    **{f"digito_{numero}": f"Digit{numero}" for numero in range(10)},
}
TECLA_ESPERA = "Standby"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TECLA = "tecla"
ACOES_DE_TRANSPORTE = {
    "tocar": "Play",
    "pausar": "Pause",
    "parar": "Stop",
    "proxima": "Next",
    "anterior": "Previous",
}

TEXTOS = {
    "en": {
        "descricao": (
            "Philips television speaking Jointspace, the sets of 2011 to 2015: power, volume, "
            "mute, input and the keys of the remote. A Philips with Android is the Android "
            "television driver instead."
        ),
        "preparo": (
            "On the television: Setup, Network settings, and leave it on the network of the "
            "hub. Nothing else is enabled and nothing is paired, because this API asks for no "
            "credential. For turning it on, fill the MAC below: a set in standby leaves the "
            "network and only a magic packet reaches it."
        ),
        "campo_api": "1 or 5, the version of the API. Empty means the hub asks the television.",
        "campo_mac": "The MAC of the television, which is what turns it on when it is off.",
        "cap_tecla": "The keys of the remote, including the two of the volume.",
    },
    "pt": {
        "descricao": (
            "Televisão Philips com Jointspace, os aparelhos de 2011 a 2015: energia, volume, "
            "mudo, entrada e as teclas do controle. Uma Philips com Android usa o driver de "
            "televisão Android."
        ),
        "preparo": (
            "Na televisão: Configuração, Rede, e deixe-a na rede do hub. Nada mais é "
            "habilitado e nada é pareado, porque esta API não pede credencial. Para LIGAR, "
            "preencha o MAC abaixo: em espera a televisão sai da rede e só o pacote mágico "
            "chega nela."
        ),
        "campo_api": "1 ou 5, a versão da API. Vazio faz o hub perguntar à televisão.",
        "campo_mac": "O MAC da televisão, que é o que a liga quando ela está desligada.",
        "cap_tecla": "As teclas do controle, incluindo as duas do volume.",
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the television."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Philips(Driver):
    """One Philips of the Jointspace generation, commanded with no credential at all."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Philips (Jointspace)", "en": "Philips television (Jointspace)"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TECLA,
            *ACOES_DE_TRANSPORTE,
        ),
        teclas=tuple(TECLAS),
        config_campos=(
            Campo(nome=CAMPO_API, tipo=TipoCampo.TEXTO, obrigatorio=False),
            Campo(nome=CAMPO_MAC, tipo=TipoCampo.TEXTO, obrigatorio=False),
        ),
        # The television announces nothing of its own on this port, so it is found by the
        # range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        textos=TEXTOS,
        marca="Philips",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._versao = ""
        self._maximo = VOLUME_MAXIMO_PADRAO
        self._minimo = 0
        self._entradas: dict[str, str] = {}
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
        if acao == ACAO_LIGAR:
            return await self._ligar()
        if acao == ACAO_DESLIGAR:
            await self._tecla(TECLA_ESPERA)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if not isinstance(valor, int) or isinstance(valor, bool) or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._escrever_audio(nivel=self._do_contrato(valor))
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._escrever_audio(mudo=valor)
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            return await self._trocar_entrada(valor)
        if acao == ACAO_TECLA:
            nome = TECLAS.get(valor) if isinstance(valor, str) else None
            if nome is None:
                return INVALID_VALUE
            await self._tecla(nome)
            return None
        if acao in ACOES_DE_TRANSPORTE:
            await self._tecla(ACOES_DE_TRANSPORTE[acao])
            return None
        return NAO_SUPORTADO

    async def _ligar(self) -> str | None:
        """A television still on the network takes the key; one in standby takes the packet."""
        if self._estado.online:
            await self._tecla(TECLA_ESPERA)
            self._defina(ligado=True)
            return None
        endereco = _mac_valido(self.cadastro.campos.get(CAMPO_MAC, ""))
        if endereco is None:
            return INVALID_VALUE
        self._fio.enviado("wake on lan")
        await asyncio.to_thread(_soprar, endereco)
        return None

    async def _trocar_entrada(self, valor: object) -> str | None:
        if not isinstance(valor, str) or not valor:
            return INVALID_VALUE
        await self._ler_entradas()
        if not self._entradas:
            # The set dropped this endpoint; a guess here would command a random input.
            return NAO_SUPORTADO
        escolhido = _id_da_entrada(self._entradas, valor)
        if escolhido is None:
            return INVALID_VALUE
        await self._escrever("sources/current", {"id": escolhido})
        self._defina(fonte=self._entradas.get(escolhido, escolhido))
        return None

    async def _ler_estado(self) -> None:
        """One poll: the audio block, which carries the level, the range and the mute."""
        audio = await self._ler("audio/volume")
        maximo = audio.get("max")
        minimo = audio.get("min")
        if isinstance(maximo, int) and maximo > 0:
            self._maximo = maximo
        if isinstance(minimo, int) and minimo >= 0:
            self._minimo = minimo
        nivel = audio.get("current")
        mudo = audio.get("muted")
        await self._ler_entradas()
        atual = await self._ler("sources/current", silencioso=True)
        escolhido = atual.get("id") if isinstance(atual.get("id"), str) else None
        self._defina(
            online=True,
            ligado=True,
            volume=self._ao_contrato(nivel) if isinstance(nivel, int) else None,
            mudo=mudo if isinstance(mudo, bool) else None,
            fonte=self._entradas.get(escolhido, escolhido) if escolhido else None,
            fontes=tuple(self._entradas.values()),
            detalhe="",
        )

    async def _ler_entradas(self) -> None:
        """The inputs as the television names them, asked once and kept."""
        if self._entradas:
            return
        bruto = await self._ler("sources", silencioso=True)
        self._entradas = {
            chave: (dado.get("name") or chave) if isinstance(dado, dict) else chave
            for chave, dado in bruto.items()
            if isinstance(chave, str)
        }

    async def _escrever_audio(self, *, nivel: int | None = None, mudo: bool | None = None) -> None:
        """The level and the mute ride the same message, so the one not being set is kept."""
        atual = self._estado
        nivel_atual = self._do_contrato(atual.volume) if atual.volume is not None else None
        mensagem = {
            "muted": atual.mudo if mudo is None else mudo,
            "current": nivel_atual if nivel is None else nivel,
        }
        if mensagem["muted"] is None or mensagem["current"] is None:
            # Half a message would write a value nobody asked for over the other field.
            raise _Falha(ERRO_APARELHO)
        await self._escrever("audio/volume", mensagem)

    async def _tecla(self, nome: str) -> None:
        await self._escrever("input/key", {"key": nome})

    def _ao_contrato(self, nivel: int) -> int:
        largura = max(1, self._maximo - self._minimo)
        return max(0, min(100, round((nivel - self._minimo) * 100 / largura)))

    def _do_contrato(self, valor: int) -> int:
        largura = max(1, self._maximo - self._minimo)
        return self._minimo + round(valor * largura / 100)

    async def _ler(self, caminho: str, *, silencioso: bool = False) -> dict:
        bruto = await self._pedir("GET", caminho, None, silencioso=silencioso)
        try:
            dado = json.loads(bruto)
        except ValueError as erro:
            raise _Falha(ERRO_APARELHO) from erro
        return dado if isinstance(dado, dict) else {}

    async def _escrever(self, caminho: str, mensagem: dict) -> None:
        await self._pedir("POST", caminho, mensagem)

    async def _pedir(
        self, metodo: str, caminho: str, mensagem: dict | None, *, silencioso: bool = False
    ) -> bytes:
        """One exchange, on the API version this television answers.

        With silencioso the endpoint is allowed to be absent: a set that dropped it answers
        404 and the caller gets an empty body instead of a failed poll.
        """
        versao = await self._versao_da_api()
        rotina = metodo == "GET"
        self._fio.enviado(f"{metodo} {caminho} {mensagem or ''}".strip(), rotina=rotina)
        estado, bruto = await self._bater(versao, metodo, caminho, mensagem)
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado == 200:
            return bruto
        if silencioso and estado == 404:
            return b"{}"
        self._fio.recusado(caminho, ERRO_APARELHO)
        raise _Falha(ERRO_APARELHO)

    async def _versao_da_api(self) -> str:
        """The version of the paths: the one of the registration, or the one it answers."""
        if self._versao:
            return self._versao
        pinada = self.cadastro.campos.get(CAMPO_API, "").strip()
        if pinada in VERSOES:
            self._versao = pinada
            return pinada
        for versao in VERSOES:
            estado, _ = await self._bater(versao, "GET", "system", None)
            if estado == 200:
                self._versao = versao
                log.info("%s: speaks the API version %s", self.cadastro.identidade, versao)
                return versao
        raise _Falha(EQ_OFFLINE)

    async def _bater(
        self, versao: str, metodo: str, caminho: str, mensagem: dict | None
    ) -> tuple[int, bytes]:
        url = f"http://{self._endereco()}:{PORTA}/{versao}/{caminho}"
        sessao = await self._abrir()
        dados = None if mensagem is None else json.dumps(mensagem).encode("utf-8")
        try:
            async with sessao.request(
                metodo,
                url,
                data=dados,
                headers={"Content-Type": "application/json"},
                allow_redirects=False,
            ) as resposta:
                return resposta.status, await corpo.inteiro(resposta.content, CORPO_MAXIMO)
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(f"{metodo} {caminho}", erro)
            raise _Falha(EQ_OFFLINE) from erro

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, ligado=False, detalhe=codigo)

    def _endereco(self) -> str:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The serial the television publishes, or the model it publishes instead."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        for versao in VERSOES:
            url = f"http://{endereco}:{PORTA}/{versao}/system"
            try:
                async with (
                    ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao,
                    sessao.get(url, allow_redirects=False) as resposta,
                ):
                    if resposta.status != 200:
                        continue
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
            except (TimeoutError, ClientError, OSError, ValueError):
                return None
            try:
                dado = json.loads(bruto)
            except ValueError:
                continue
            if not isinstance(dado, dict):
                continue
            for chave in ("serialnumber", "model", "name"):
                valor = dado.get(chave)
                if isinstance(valor, str) and valor.strip():
                    return valor.strip()
        return None


def _id_da_entrada(entradas: dict[str, str], valor: str) -> str | None:
    """The id of an input by its id or by the name the owner reads on the screen."""
    if valor in entradas:
        return valor
    procurado = valor.strip().casefold()
    for chave, nome in entradas.items():
        if nome.strip().casefold() == procurado:
            return chave
    return None


def _mac_valido(bruto: str) -> bytes | None:
    limpo = bruto.strip()
    if not _MAC.match(limpo):
        return None
    return bytes.fromhex(limpo.replace(":", "").replace("-", ""))


def _soprar(endereco: bytes) -> None:
    """One magic packet to the broadcast of the segment, which is what wakes the television."""
    pacote = b"\xff" * 6 + endereco * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as soquete:
        soquete.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        soquete.sendto(pacote, ("255.255.255.255", PORTA_WOL))
