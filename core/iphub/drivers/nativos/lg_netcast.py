# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""LG televisions of the NetCast generation, the one before webOS.

What the protocol is, so nobody has to read it again:

- Plain HTTP on port 8080 under /roap/api/, with XML in and XML out, and no TLS anywhere.
- Pairing is two steps and a key on the screen: a POST of AuthKeyReq makes the television
  show six digits, and a POST of AuthReq with those digits answers a SESSION. The key is
  kept in the registration and the session is asked for again on every poll, because the
  television forgets it.
- A key of the remote is a NUMBER, sent as HandleKeyInput inside the session. Everything the
  driver commands is one of those numbers.
- The volume is READ from the television and cannot be set: the protocol only has the two
  step keys, so volume is not declared as a capability and the keys are. That is the same
  rule the television of the other generation taught, and it is what keeps the panel from
  drawing a bar that refuses every drag.
- The television cannot be turned ON over the network, because the network of it dies with
  it: turning it on is a Wake on LAN packet to the MAC of the registration.

This is NOT the webOS driver: a television with webOS speaks a WebSocket on another port,
and the two generations share nothing but the brand.
"""

import asyncio
import logging
import re
import socket
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import Cadastro, ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.lg_netcast")

TIPO = "tv_lg_netcast"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
AUTH_PENDENTE = "auth_pendente"
AGUARDANDO = "aguardando"
FALHOU = "falhou"
PAREADO = "pareado"

PORTA = 8080
CAMINHO = "/roap/api"
TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

CABECALHO = '<?xml version="1.0" encoding="utf-8"?>'
TIPO_DO_CORPO = "application/atom+xml"

CAMPO_CHAVE = "chave"
CAMPO_MAC = "mac"

PORTA_WOL = 9
_MAC = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}$")
_SO_DIGITOS = re.compile(r"^[0-9]+$")

# The keys of the remote, as the numbers this protocol carries.
TECLAS = {
    "cima": 12,
    "baixo": 13,
    "esquerda": 14,
    "direita": 15,
    "ok": 20,
    "inicio": 21,
    "voltar": 23,
    "mais": 24,
    "menos": 25,
    "canal_mais": 27,
    "canal_menos": 28,
    "play_pause": 33,
    "guia": 44,
    "info": 45,
    "menu": 405,
    "sair": 412,
    **{f"digito_{numero}": 2 + numero for numero in range(10)},
}
TECLA_ENERGIA = 1
TECLA_MUDO = 26
TECLA_PARAR = 35
TECLA_PROXIMA = 38
TECLA_ANTERIOR = 39
TECLA_PAUSAR = 34
TECLA_TOCAR = 33

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_MUDO = "mudo"
ACAO_TECLA = "tecla"
ACOES_DE_TRANSPORTE = {
    "tocar": TECLA_TOCAR,
    "pausar": TECLA_PAUSAR,
    "parar": TECLA_PARAR,
    "proxima": TECLA_PROXIMA,
    "anterior": TECLA_ANTERIOR,
}

TEXTOS = {
    "en": {
        "descricao": (
            "LG television of the NetCast generation, the one before webOS: power, mute and "
            "the keys of the remote. A television with webOS is the other driver."
        ),
        "preparo": (
            "On the television: Settings, Network, and leave it on the network of the hub. "
            "For turning it ON, the television has to have Simplink or the wake setting of "
            "its year enabled, and the MAC below filled, because the network of a NetCast "
            "dies with the television."
        ),
        "auth_ajuda": (
            "1. Press pair. The television shows six digits on the screen.\n"
            "2. Type them in the key field of this registration and save.\n"
            "3. Press pair again. The key is kept, so this is done once."
        ),
        "campo_chave": "The six digits the television shows while pairing.",
        "campo_mac": "The MAC of the television, which is what turns it on.",
        "cap_tecla": "The keys of the remote, including the two of the volume.",
    },
    "pt": {
        "descricao": (
            "Televisão LG da geração NetCast, a anterior ao webOS: energia, mudo e as teclas "
            "do controle. Uma televisão com webOS é o outro driver."
        ),
        "preparo": (
            "Na televisão: Configurações, Rede, e deixe-a na rede do hub. Para LIGAR, a "
            "televisão precisa do Simplink ou da opção de despertar do ano dela habilitada, e "
            "do MAC abaixo preenchido, porque a rede de uma NetCast morre com a televisão."
        ),
        "auth_ajuda": (
            "1. Aperte parear. A televisão mostra seis dígitos na tela.\n"
            "2. Digite-os no campo chave deste cadastro e salve.\n"
            "3. Aperte parear de novo. A chave fica guardada, então isso é feito uma vez."
        ),
        "campo_chave": "Os seis dígitos que a televisão mostra ao parear.",
        "campo_mac": "O MAC da televisão, que é o que a liga.",
        "cap_tecla": "As teclas do controle, incluindo as duas do volume.",
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the television."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class LgNetcast(Driver):
    """One LG television of the NetCast generation, paired by a key shown on its screen."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV LG (NetCast)", "en": "LG television (NetCast)"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_MUDO,
            ACAO_TECLA,
            *ACOES_DE_TRANSPORTE,
        ),
        teclas=tuple(TECLAS),
        auth=Auth.CODIGO,
        config_campos=(
            Campo(nome=CAMPO_CHAVE, tipo=TipoCampo.SEGREDO, obrigatorio=False),
            Campo(nome=CAMPO_MAC, tipo=TipoCampo.TEXTO, obrigatorio=False),
        ),
        # The webOS driver claims the announcement of an LG television, so this one is found
        # by the range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        textos=TEXTOS,
        marca="LG",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._id_da_sessao = ""
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        self._id_da_sessao = ""
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def autenticar(self) -> str:
        """Pareado, aguardando while the key is on the screen, or falhou."""
        chave = self._chave()
        try:
            if not chave:
                await self._pedir("auth", f"{CABECALHO}<auth><type>AuthKeyReq</type></auth>")
                return AGUARDANDO
            await self._abrir_sessao()
        except _Falha as falha:
            log.warning("%s: pairing failed with %s", self._id(), falha.codigo)
            return FALHOU
        log.info("%s: paired", self._id())
        return PAREADO

    async def atualizar(self) -> None:
        try:
            await self._ler_estado()
        except _Falha as falha:
            self._id_da_sessao = ""
            self._falhar(falha.codigo)
            return
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            if falha.codigo != INVALID_VALUE:
                self._id_da_sessao = ""
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            # The network of a NetCast dies with the television, so the only way in is the
            # magic packet, and without a MAC there is nowhere to send it.
            endereco = _mac_valido(self.cadastro.campos.get(CAMPO_MAC, ""))
            if endereco is None:
                return INVALID_VALUE
            self._fio.enviado("wake on lan")
            await asyncio.to_thread(_soprar, endereco)
            return None
        if acao == ACAO_DESLIGAR:
            await self._tecla(TECLA_ENERGIA)
            self._defina(ligado=False)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            # The television has one key for mute and reports the state it landed on.
            if self._estado.mudo is valor:
                return None
            await self._tecla(TECLA_MUDO)
            return None
        if acao == ACAO_TECLA:
            codigo = TECLAS.get(valor) if isinstance(valor, str) else None
            if codigo is None:
                return INVALID_VALUE
            await self._tecla(codigo)
            return None
        if acao in ACOES_DE_TRANSPORTE:
            await self._tecla(ACOES_DE_TRANSPORTE[acao])
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: the volume block of the television, which carries the mute with it.

        The session comes first because the television only answers a question inside one,
        and asking for a session is what says a registration was never paired.
        """
        await self._abrir_sessao()
        bruto = await self._buscar("volume_info")
        raiz = _arvore(bruto)
        dado = raiz.find(".//data") if raiz is not None else None
        if dado is None:
            raise _Falha(ERRO_APARELHO)
        nivel = _texto(dado, "level")
        mudo = _texto(dado, "mute")
        self._defina(
            online=True,
            ligado=True,
            # The level travels for the record and never as a capability: this protocol has
            # no way to set one, and a bar that refuses every drag is the defect already paid
            # for on the television of the other generation.
            volume=int(nivel) if _SO_DIGITOS.match(nivel) else None,
            mudo=mudo == "true" if mudo else None,
            detalhe="",
        )

    async def _tecla(self, codigo: int) -> None:
        sessao = await self._abrir_sessao()
        mensagem = (
            f"{CABECALHO}<command><session>{sessao}</session>"
            f"<type>HandleKeyInput</type><value>{codigo}</value></command>"
        )
        await self._pedir("command", mensagem)

    async def _abrir_sessao(self) -> str:
        """The session of this exchange, asked with the key the pairing left behind."""
        if self._id_da_sessao:
            return self._id_da_sessao
        chave = self._chave()
        if not chave:
            raise _Falha(AUTH_PENDENTE)
        mensagem = f"{CABECALHO}<auth><type>AuthReq</type><value>{chave}</value></auth>"
        bruto = await self._pedir("auth", mensagem)
        raiz = _arvore(bruto)
        sessao = _texto(raiz, "session") if raiz is not None else ""
        if not sessao:
            raise _Falha(AUTH_PENDENTE)
        self._id_da_sessao = sessao
        return sessao

    async def _pedir(self, caminho: str, mensagem: str) -> bytes:
        url = f"http://{self._endereco()}:{PORTA}{CAMINHO}/{caminho}"
        # The key of the pairing rides inside this body, and the transcript never carries it.
        self._fio.enviado(f"POST {caminho}")
        sessao = await self._abrir()
        try:
            async with sessao.post(
                url,
                data=mensagem.encode("utf-8"),
                headers={"Content-Type": TIPO_DO_CORPO},
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(f"POST {caminho}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}")
        if estado == 401:
            raise _Falha(AUTH_PENDENTE)
        if estado != 200:
            raise _Falha(ERRO_APARELHO)
        return bruto

    async def _buscar(self, alvo: str) -> bytes:
        url = f"http://{self._endereco()}:{PORTA}{CAMINHO}/data"
        self._fio.enviado(f"GET data {alvo}", rotina=True)
        sessao = await self._abrir()
        try:
            async with sessao.get(url, params={"target": alvo}, allow_redirects=False) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(f"GET data {alvo}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=True)
        if estado == 401:
            raise _Falha(AUTH_PENDENTE)
        if estado != 200:
            raise _Falha(ERRO_APARELHO)
        return bruto

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
        self._defina(online=False, ligado=False, detalhe=codigo)

    def _chave(self) -> str:
        return self.cadastro.segredos.get(CAMPO_CHAVE, "").strip()

    def _endereco(self) -> str:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco

    def _id(self) -> str:
        return self.cadastro.identidade

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The uuid the television answers with no pairing at all."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        url = f"http://{endereco}:{PORTA}{CAMINHO}/data"
        try:
            async with (
                ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao,
                sessao.get(
                    url,
                    params={"target": "rootservice.xml"},
                    headers={"User-Agent": "UDAP/2.0"},
                    allow_redirects=False,
                ) as resposta,
            ):
                if resposta.status != 200:
                    return None
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
        except (TimeoutError, ClientError, OSError, ValueError):
            return None
        raiz = _arvore(bruto)
        if raiz is None:
            return None
        return _texto(raiz, "device/uuid") or None


def _arvore(bruto: bytes) -> ElementTree.Element | None:
    # A DTD is what an entity bomb needs, and a television never sends one.
    if not bruto or re.search(rb"<!(?:DOCTYPE|ENTITY)", bruto, re.IGNORECASE):
        return None
    try:
        return ElementTree.fromstring(bruto)
    except (ElementTree.ParseError, ValueError):
        return None


def _texto(elemento: ElementTree.Element | None, caminho: str) -> str:
    if elemento is None:
        return ""
    achado = elemento.find(caminho)
    return (achado.text or "").strip() if achado is not None else ""


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
