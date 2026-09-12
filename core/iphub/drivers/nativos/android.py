# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Android TV and Google TV over the remote protocol of the television itself.

What the protocol is, so nobody has to read it again:

- Two TLS ports on the television, 6467 to pair and 6466 to command, both of them asking the
  CLIENT for a certificate. So this driver owns an RSA key and a self signed certificate of
  its own, generated once per registration and kept as a secret of it: the pairing binds the
  television to that certificate, and a new one means walking to the television again.
- Pairing is a handshake of six messages over 6467. At the end of it the television shows six
  hexadecimal characters on the screen, and the secret that closes the pairing is the SHA256
  of the modulus and the exponent of BOTH certificates plus the last four of those characters.
  The first two are a check byte of that same hash, which is what makes a mistyped code a
  refusal here instead of a failure on the television.
- Commands go over 6466 on ONE long connection the television drives: it configures, it asks
  to be pinged back, and it pushes power, volume and the app in front. A connection that stops
  answering the ping is dropped by the television, so the reader answers them and nothing
  else keeps it alive.
- Messages are protocol buffers, length delimited. The encoding is in drivers/protobuf.py and
  the numbers of every field this driver writes or reads are the constants below.
- The volume the television reports is a level and a maximum, and the protocol has no way to
  set a level: the keys are what changes it. So volume is NOT declared as a capability, which
  is what keeps the panel from drawing a bar that refuses every drag.
"""

import asyncio
import hashlib
import logging
import os
import ssl
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio, protobuf
from iphub.drivers.base import NAO_SUPORTADO, PAREADO, Driver
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.android")

TIPO = "tv_android"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
AUTH_PENDENTE = "auth_pendente"
AGUARDANDO = "aguardando"
FALHOU = "falhou"

PORTA_PAREAMENTO = 6467
PORTA_REMOTO = 6466
CONEXAO_S = 5.0
PASSO_S = 10.0
PARTIDA_S = 15.0
FALHAS_ATE_OFFLINE = 2

CAMPO_CODIGO = "codigo"
CAMPO_CERTIFICADO = "certificado"
CAMPO_CHAVE = "chave"

NOME_DO_CLIENTE = "QA IP Hub"
SERVICO = "atvremote"
PACOTE = "atvremote"
VERSAO_DO_APP = "1.0.0"

# Field numbers of the pairing message. The outer message carries the version and the status
# and exactly one of the six below.
P_VERSAO = 1
P_STATUS = 2
P_PEDIDO = 10
P_PEDIDO_ACK = 11
P_OPCOES = 20
P_CONFIGURACAO = 30
P_CONFIGURACAO_ACK = 31
P_SEGREDO = 40
P_SEGREDO_ACK = 41
P_SERVICO = 1
P_CLIENTE = 2
P_ENTRADAS = 1
P_PAPEL_PREFERIDO = 3
P_CODIFICACAO = 1
P_PAPEL = 2
P_TIPO_DE_CODIFICACAO = 1
P_TAMANHO = 2
P_BYTES_DO_SEGREDO = 1

VERSAO_DO_PROTOCOLO = 2
STATUS_OK = 200
PAPEL_ENTRADA = 1
CODIFICACAO_HEXADECIMAL = 3
DIGITOS_DO_CODIGO = 6

# Field numbers of the remote message.
R_CONFIGURAR = 1
R_ATIVAR = 2
R_ERRO = 3
R_PING = 8
R_PONG = 9
R_TECLA = 10
R_APP_EM_FRENTE = 20
R_INICIO = 40
R_VOLUME = 50
R_ABRIR_APP = 90
R_RECURSOS = 1
R_APARELHO = 2
R_MODELO = 1
R_FABRICANTE = 2
R_DESCONHECIDO1 = 3
R_DESCONHECIDO2 = 4
R_PACOTE = 5
R_VERSAO_DO_APP = 6
R_ATIVO = 1
R_VAL1 = 1
R_CODIGO_DA_TECLA = 1
R_DIRECAO = 2
R_LIGADA = 1
R_VOLUME_MAXIMO = 6
R_VOLUME_NIVEL = 7
R_VOLUME_MUDO = 8
R_LINK = 1
R_INFO_DO_APP = 1
R_PACOTE_DO_APP = 12

# What this client asks to do: ping, keys, power, volume and app links. The television answers
# with what it supports and the two are intersected, so a television without one of them is
# never sent a message it would answer with an error.
RECURSO_PING = 1 << 0
RECURSO_TECLA = 1 << 1
RECURSO_ENERGIA = 1 << 5
RECURSO_VOLUME = 1 << 6
RECURSO_APP = 1 << 9
RECURSOS = RECURSO_PING | RECURSO_TECLA | RECURSO_ENERGIA | RECURSO_VOLUME | RECURSO_APP

TECLA_CURTA = 3

CODIGOS_DE_TECLA = {
    "inicio": 3,
    "voltar": 4,
    "digito_0": 7,
    "digito_1": 8,
    "digito_2": 9,
    "digito_3": 10,
    "digito_4": 11,
    "digito_5": 12,
    "digito_6": 13,
    "digito_7": 14,
    "digito_8": 15,
    "digito_9": 16,
    "cima": 19,
    "baixo": 20,
    "esquerda": 21,
    "direita": 22,
    "ok": 23,
    "mais": 24,
    "menos": 25,
    "menu": 82,
    "play_pause": 85,
    "proxima": 87,
    "anterior": 88,
    "info": 165,
    "canal_mais": 166,
    "canal_menos": 167,
    "guia": 172,
    "sair": 178,
}
TECLA_ENERGIA = 26
TECLA_MUDO = 164
TECLA_PARAR = 86

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_MUDO = "mudo"
ACAO_TECLA = "tecla"
ACAO_ATALHO = "atalho"
ACOES_DE_TRANSPORTE = {
    "tocar": 85,
    "pausar": 85,
    "parar": TECLA_PARAR,
    "proxima": 87,
    "anterior": 88,
}

TEXTOS = {
    "en": {
        "descricao": (
            "Android TV and Google TV over the remote protocol of the television, which needs "
            "no cloud and no account: power, the keys of the remote, and opening an app."
        ),
        "preparo": (
            "On the television: Settings, Apps, See all apps, Show system apps, Android TV "
            "Remote Service, and leave it enabled. The television must be on for pairing, and "
            "the network of it must be the network of the hub."
        ),
        "auth_ajuda": (
            "1. Press pair. The television shows six characters on the screen.\n"
            "2. Type them in the code field of this registration and save.\n"
            "3. Press pair again. The code lives for about two minutes; when it expires, "
            "press pair to get a new one."
        ),
        "cap_atalho": (
            "An app is opened by its link. market://launch?id=<package> opens any installed "
            "app, and a deep link of the app itself opens a title inside it."
        ),
        "campo_codigo": "The six characters the television shows while pairing.",
        "campo_certificado": (
            "Generated by the hub while pairing, and kept because the television binds the "
            "pairing to it. Nothing to type here."
        ),
        "campo_chave": "The private key of that certificate. Nothing to type here.",
    },
    "pt": {
        "descricao": (
            "TV Android e Google TV pelo protocolo de controle da própria televisão, que não "
            "precisa de nuvem nem de conta: energia, as teclas do controle e abrir um app."
        ),
        "preparo": (
            "Na televisão: Configurações, Apps, Ver todos os apps, Mostrar apps do sistema, "
            "Android TV Remote Service, e deixe habilitado. A televisão precisa estar ligada "
            "para parear, e a rede dela precisa ser a rede do hub."
        ),
        "auth_ajuda": (
            "1. Aperte parear. A televisão mostra seis caracteres na tela.\n"
            "2. Digite-os no campo código deste cadastro e salve.\n"
            "3. Aperte parear de novo. O código vale cerca de dois minutos; quando expirar, "
            "aperte parear para receber um novo."
        ),
        "cap_atalho": (
            "Um app é aberto pelo link dele. market://launch?id=<pacote> abre qualquer app "
            "instalado, e um link do próprio app abre um título dentro dele."
        ),
        "campo_codigo": "Os seis caracteres que a televisão mostra ao parear.",
        "campo_certificado": (
            "Gerado pelo hub ao parear, e guardado porque a televisão prende o pareamento "
            "a ele. Não há nada a digitar aqui."
        ),
        "campo_chave": "A chave privada desse certificado. Não há nada a digitar aqui.",
    },
}

SUGESTOES = (
    Sugestao("atalhos", "Netflix", "market://launch?id=com.netflix.ninja"),
    Sugestao("atalhos", "YouTube", "market://launch?id=com.google.android.youtube.tv"),
    Sugestao("atalhos", "Prime Video", "market://launch?id=com.amazon.amazonvideo.livingroom"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the television."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class TvAndroid(Driver):
    """One Android TV, paired by a code and commanded over its own remote protocol."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Android (Google TV)", "en": "Android TV (Google TV)"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_MUDO,
            ACAO_TECLA,
            ACAO_ATALHO,
            "tocar",
            "pausar",
            "parar",
            "proxima",
            "anterior",
        ),
        teclas=tuple(CODIGOS_DE_TECLA),
        auth=Auth.CODIGO,
        descoberta=Descoberta(mdns_servicos=("_androidtvremote2._tcp.local.",)),
        config_campos=(
            Campo(nome=CAMPO_CODIGO, tipo=TipoCampo.TEXTO, obrigatorio=False),
            # The certificate and the key are generated by the hub while pairing and handed
            # to the caller to persist; they are fields so they land in the secrets, and the
            # operator never types them.
            Campo(nome=CAMPO_CERTIFICADO, tipo=TipoCampo.SEGREDO, obrigatorio=False),
            Campo(nome=CAMPO_CHAVE, tipo=TipoCampo.SEGREDO, obrigatorio=False),
        ),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Android TV",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._certificado = cadastro.segredos.get(CAMPO_CERTIFICADO, "")
        self._chave = cadastro.segredos.get(CAMPO_CHAVE, "")
        self._pareando: _Pareamento | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._ouvinte: asyncio.Task | None = None
        self._pronta: asyncio.Future[bool] | None = None
        self._recursos = RECURSOS
        self._falhas = 0
        self._transcricao: fio.Fio | None = None

    def _fio(self) -> fio.Fio:
        if self._transcricao is None:
            self._transcricao = fio.Fio(log, self.cadastro.identidade)
        return self._transcricao

    async def parar(self) -> None:
        pareando, self._pareando = self._pareando, None
        if pareando is not None:
            await pareando.fechar()
        await self._largar()

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The certificate the pairing bound to this television, for the caller to persist."""
        if not self._certificado or not self._chave:
            return {}
        return {CAMPO_CERTIFICADO: self._certificado, CAMPO_CHAVE: self._chave}

    async def autenticar(self) -> str:
        """Pareado, aguardando while the code is on the screen, or falhou."""
        codigo = self.cadastro.campos.get(CAMPO_CODIGO, "").strip()
        try:
            if self._pareando is None or self._pareando.morta():
                await self._comecar_a_parear()
                return AGUARDANDO
            if not codigo:
                return AGUARDANDO
            await self._pareando.terminar(codigo)
        except _Falha as falha:
            log.warning("%s: pairing failed with %s", self._id(), falha.codigo)
            await self._soltar_pareamento()
            return FALHOU
        except (TimeoutError, OSError, ssl.SSLError) as erro:
            log.warning("%s: pairing failed: %s", self._id(), erro)
            await self._soltar_pareamento()
            return FALHOU
        await self._soltar_pareamento()
        log.info("%s: paired", self._id())
        return PAREADO

    async def _comecar_a_parear(self) -> None:
        """Opens the pairing socket and walks it to where the television shows the code."""
        await self._soltar_pareamento()
        if not self._certificado or not self._chave:
            self._certificado, self._chave = await asyncio.to_thread(_gerar_certificado)
        pareamento = _Pareamento(self._endereco(), self._certificado, self._chave, self._fio())
        await pareamento.comecar()
        self._pareando = pareamento

    async def _soltar_pareamento(self) -> None:
        pareando, self._pareando = self._pareando, None
        if pareando is not None:
            await pareando.fechar()

    async def atualizar(self) -> None:
        """One poll: the connection is what carries the state, so this keeps it up."""
        if self._ouvinte is not None and not self._ouvinte.done():
            return
        try:
            await self._conectar()
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        except (TimeoutError, OSError, ssl.SSLError, protobuf.Ilegivel) as erro:
            self._fio().falhou("connect", erro)
            self._falhar(EQ_OFFLINE)
            return
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo
        except (TimeoutError, OSError, ssl.SSLError) as erro:
            self._fio().falhou(acao, erro)
            await self._largar()
            return EQ_OFFLINE

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._energia(ligada=True)
        if acao == ACAO_DESLIGAR:
            return await self._energia(ligada=False)
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            # The television has one key for mute and reports the state it landed on, so a
            # command that asks for the state it is already in is done and sends nothing.
            if self._estado.mudo is valor:
                return None
            await self._tecla(TECLA_MUDO)
            return None
        if acao == ACAO_TECLA:
            codigo = CODIGOS_DE_TECLA.get(valor) if isinstance(valor, str) else None
            if codigo is None:
                return INVALID_VALUE
            await self._tecla(codigo)
            return None
        if acao in ACOES_DE_TRANSPORTE:
            await self._tecla(ACOES_DE_TRANSPORTE[acao])
            return None
        if acao == ACAO_ATALHO:
            if not isinstance(valor, str) or not valor.strip():
                return INVALID_VALUE
            await self._mandar(
                protobuf.campo_mensagem(R_ABRIR_APP, protobuf.campo_texto(R_LINK, valor.strip())),
                f"open {valor.strip()}",
            )
            return None
        return NAO_SUPORTADO

    async def _energia(self, *, ligada: bool) -> str | None:
        """The television has one power key, so the state it is in decides whether to send it."""
        if self._estado.ligado is ligada:
            return None
        await self._tecla(TECLA_ENERGIA)
        return None

    async def _tecla(self, codigo: int) -> None:
        corpo = protobuf.campo_inteiro(R_CODIGO_DA_TECLA, codigo) + protobuf.campo_inteiro(
            R_DIRECAO, TECLA_CURTA
        )
        await self._mandar(protobuf.campo_mensagem(R_TECLA, corpo), f"key {codigo}")

    async def _mandar(self, mensagem: bytes, o_que: str) -> None:
        escritor = self._escritor
        if escritor is None or escritor.is_closing():
            raise _Falha(EQ_OFFLINE)
        self._fio().enviado(o_que)
        escritor.write(protobuf.quadro(mensagem))
        await escritor.drain()

    async def _conectar(self) -> None:
        """Opens the command connection and waits for the television to say it is ready."""
        await self._largar()
        if not self._certificado or not self._chave:
            raise _Falha(AUTH_PENDENTE)
        contexto = _contexto_tls(self._certificado, self._chave)
        endereco, porta = self._endereco()
        async with asyncio.timeout(CONEXAO_S):
            leitor, escritor = await asyncio.open_connection(
                endereco, porta, ssl=contexto, server_hostname=None
            )
        self._escritor = escritor
        self._pronta = asyncio.get_running_loop().create_future()
        self._ouvinte = asyncio.create_task(self._ouvir(leitor, escritor), name="android-tv")
        try:
            async with asyncio.timeout(PARTIDA_S):
                await self._pronta
        except TimeoutError:
            await self._largar()
            raise _Falha(EQ_OFFLINE) from None
        self._fio().reconectou()

    async def _ouvir(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        """Reads the connection for as long as it lives, answering what has to be answered."""
        buffer = bytearray()
        try:
            while True:
                pedaco = await leitor.read(4096)
                if not pedaco:
                    break
                buffer += pedaco
                for corpo in protobuf.quadros(buffer):
                    await self._receber(corpo, escritor)
        except (TimeoutError, OSError, ssl.SSLError, protobuf.Ilegivel) as erro:
            self._fio().falhou("read", erro)
        finally:
            if not escritor.is_closing():
                escritor.close()
            # A connection that dropped is not an offline television yet: the poll that
            # follows tries again, and two failed polls in a row is what says offline.
            pronta = self._pronta
            if pronta is not None and not pronta.done():
                pronta.set_result(False)

    async def _receber(self, corpo: bytes, escritor: asyncio.StreamWriter) -> None:
        campos = protobuf.ler(corpo)
        if protobuf.tem(campos, R_PING):
            val1 = protobuf.inteiro(protobuf.mensagem(campos, R_PING), R_VAL1)
            resposta = protobuf.campo_mensagem(R_PONG, protobuf.campo_inteiro(R_VAL1, val1))
            self._fio().recebido("ping", rotina=True)
            escritor.write(protobuf.quadro(resposta))
            await escritor.drain()
            return
        if protobuf.tem(campos, R_CONFIGURAR):
            await self._configurar(protobuf.mensagem(campos, R_CONFIGURAR), escritor)
            return
        if protobuf.tem(campos, R_ATIVAR):
            self._fio().recebido("set active")
            corpo_ativo = protobuf.campo_mensagem(
                R_ATIVAR, protobuf.campo_inteiro(R_ATIVO, self._recursos)
            )
            escritor.write(protobuf.quadro(corpo_ativo))
            await escritor.drain()
            return
        if protobuf.tem(campos, R_INICIO):
            ligada = bool(protobuf.inteiro(protobuf.mensagem(campos, R_INICIO), R_LIGADA))
            self._fio().recebido(f"started {ligada}")
            self._defina(online=True, ligado=ligada, detalhe="")
            pronta = self._pronta
            if pronta is not None and not pronta.done():
                pronta.set_result(True)
            return
        if protobuf.tem(campos, R_VOLUME):
            volume = protobuf.mensagem(campos, R_VOLUME)
            maximo = protobuf.inteiro(volume, R_VOLUME_MAXIMO)
            nivel = protobuf.inteiro(volume, R_VOLUME_NIVEL)
            mudo = bool(protobuf.inteiro(volume, R_VOLUME_MUDO))
            self._fio().recebido(f"volume {nivel}/{maximo} muted {mudo}")
            # The level travels for the record and never as a capability: setting one is not
            # in this protocol, and a state nobody can command is read only by definition.
            self._defina(mudo=mudo, volume=_de_zero_a_cem(nivel, maximo))
            return
        if protobuf.tem(campos, R_APP_EM_FRENTE):
            app = protobuf.mensagem(campos, R_APP_EM_FRENTE)
            pacote = protobuf.texto(protobuf.mensagem(app, R_INFO_DO_APP), R_PACOTE_DO_APP)
            if pacote:
                self._fio().recebido(f"app {pacote}")
                self._defina(detalhe="")
            return
        if protobuf.tem(campos, R_ERRO):
            self._fio().recusado("command", "erro_aparelho")

    async def _configurar(self, pedido: dict, escritor: asyncio.StreamWriter) -> None:
        aparelho = protobuf.mensagem(pedido, R_APARELHO)
        fabricante = protobuf.texto(aparelho, R_FABRICANTE)
        modelo = protobuf.texto(aparelho, R_MODELO)
        self._recursos = RECURSOS & protobuf.inteiro(pedido, R_RECURSOS, RECURSOS)
        self._fio().recebido(f"configure {fabricante} {modelo} features {self._recursos}")
        meu = (
            protobuf.campo_texto(R_MODELO, NOME_DO_CLIENTE)
            + protobuf.campo_texto(R_FABRICANTE, NOME_DO_CLIENTE)
            + protobuf.campo_inteiro(R_DESCONHECIDO1, 1)
            + protobuf.campo_texto(R_DESCONHECIDO2, "1")
            + protobuf.campo_texto(R_PACOTE, PACOTE)
            + protobuf.campo_texto(R_VERSAO_DO_APP, VERSAO_DO_APP)
        )
        corpo = protobuf.campo_inteiro(R_RECURSOS, self._recursos) + protobuf.campo_mensagem(
            R_APARELHO, meu
        )
        escritor.write(protobuf.quadro(protobuf.campo_mensagem(R_CONFIGURAR, corpo)))
        await escritor.drain()

    async def _largar(self) -> None:
        ouvinte, self._ouvinte = self._ouvinte, None
        escritor, self._escritor = self._escritor, None
        self._pronta = None
        if escritor is not None and not escritor.is_closing():
            escritor.close()
            with suppress(TimeoutError, OSError, ssl.SSLError):
                await escritor.wait_closed()
        if ouvinte is not None:
            ouvinte.cancel()
            with suppress(asyncio.CancelledError):
                await ouvinte

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, detalhe=codigo)

    def _id(self) -> str:
        return self.cadastro.identidade

    def _endereco(self) -> tuple[str, int]:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(INVALID_VALUE)
        return endereco, PORTA_REMOTO

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The fingerprint of the certificate of the television, which it keeps for good.

        The certificate travels in the TLS handshake before any pairing, so an address can be
        recognized with no registration at all. A television that refuses the handshake
        without a client certificate answers nothing, and the sweep says so.
        """
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
        try:
            async with asyncio.timeout(CONEXAO_S):
                leitor, escritor = await asyncio.open_connection(
                    endereco, PORTA_REMOTO, ssl=contexto, server_hostname=None
                )
        except (TimeoutError, OSError, ssl.SSLError):
            return None
        try:
            objeto = escritor.get_extra_info("ssl_object")
            bruto = objeto.getpeercert(True) if objeto is not None else None
        finally:
            escritor.close()
            with suppress(TimeoutError, OSError, ssl.SSLError):
                await escritor.wait_closed()
        del leitor
        return hashlib.sha256(bruto).hexdigest()[:32] if bruto else None


class _Pareamento:
    """The pairing socket while the code is on the screen of the television."""

    def __init__(
        self, endereco: tuple[str, int], certificado: str, chave: str, transcricao: fio.Fio
    ) -> None:
        self._endereco = (endereco[0], PORTA_PAREAMENTO)
        self._certificado = certificado
        self._chave = chave
        self._fio = transcricao
        self._leitor: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._buffer = bytearray()
        self._modulo_da_tv = 0
        self._expoente_da_tv = 0

    def morta(self) -> bool:
        escritor = self._escritor
        return escritor is None or escritor.is_closing()

    async def fechar(self) -> None:
        escritor, self._escritor = self._escritor, None
        self._leitor = None
        if escritor is not None and not escritor.is_closing():
            escritor.close()
            with suppress(TimeoutError, OSError, ssl.SSLError):
                await escritor.wait_closed()

    async def comecar(self) -> None:
        """Walks the handshake to the point where the television shows the code."""
        contexto = _contexto_tls(self._certificado, self._chave)
        async with asyncio.timeout(CONEXAO_S):
            self._leitor, self._escritor = await asyncio.open_connection(
                self._endereco[0], self._endereco[1], ssl=contexto, server_hostname=None
            )
        objeto = self._escritor.get_extra_info("ssl_object")
        bruto = objeto.getpeercert(True) if objeto is not None else None
        if not bruto:
            raise _Falha(FALHOU)
        self._modulo_da_tv, self._expoente_da_tv = _numeros_do_certificado(
            x509.load_der_x509_certificate(bruto)
        )
        pedido = protobuf.campo_texto(P_SERVICO, SERVICO) + protobuf.campo_texto(
            P_CLIENTE, NOME_DO_CLIENTE
        )
        await self._trocar(protobuf.campo_mensagem(P_PEDIDO, pedido), "pairing request")
        await self._esperar(P_PEDIDO_ACK)
        codificacao = protobuf.campo_inteiro(
            P_TIPO_DE_CODIFICACAO, CODIFICACAO_HEXADECIMAL
        ) + protobuf.campo_inteiro(P_TAMANHO, DIGITOS_DO_CODIGO)
        opcoes = protobuf.campo_mensagem(P_ENTRADAS, codificacao) + protobuf.campo_inteiro(
            P_PAPEL_PREFERIDO, PAPEL_ENTRADA
        )
        await self._trocar(protobuf.campo_mensagem(P_OPCOES, opcoes), "options")
        await self._esperar(P_OPCOES)
        configuracao = protobuf.campo_mensagem(P_CODIFICACAO, codificacao) + protobuf.campo_inteiro(
            P_PAPEL, PAPEL_ENTRADA
        )
        await self._trocar(protobuf.campo_mensagem(P_CONFIGURACAO, configuracao), "configuration")
        await self._esperar(P_CONFIGURACAO_ACK)

    async def terminar(self, codigo: str) -> None:
        """Sends the secret the code proves, which is what closes the pairing."""
        segredo = self._segredo(codigo)
        await self._trocar(
            protobuf.campo_mensagem(P_SEGREDO, protobuf.campo_bytes(P_BYTES_DO_SEGREDO, segredo)),
            "secret",
        )
        await self._esperar(P_SEGREDO_ACK)

    def _segredo(self, codigo: str) -> bytes:
        """SHA256 over both certificates and the code, checked against the code itself.

        The first two characters of the code are the first byte of that same hash, so a
        mistyped code is refused here, before the television is asked anything.
        """
        limpo = codigo.strip().replace(" ", "")
        if len(limpo) != DIGITOS_DO_CODIGO:
            raise _Falha(INVALID_VALUE)
        try:
            bytes.fromhex(limpo)
        except ValueError:
            raise _Falha(INVALID_VALUE) from None
        meu = x509.load_pem_x509_certificate(self._certificado.encode("ascii"))
        modulo, expoente = _numeros_do_certificado(meu)
        digestor = hashlib.sha256()
        for numero in (modulo, expoente, self._modulo_da_tv, self._expoente_da_tv):
            digestor.update(_em_bytes(numero))
        digestor.update(bytes.fromhex(limpo[2:]))
        resumo = digestor.digest()
        if resumo[0] != int(limpo[:2], 16):
            raise _Falha(INVALID_VALUE)
        return resumo

    async def _trocar(self, corpo: bytes, o_que: str) -> None:
        escritor = self._escritor
        if escritor is None or escritor.is_closing():
            raise _Falha(EQ_OFFLINE)
        self._fio.enviado(o_que)
        mensagem = (
            protobuf.campo_inteiro(P_VERSAO, VERSAO_DO_PROTOCOLO)
            + protobuf.campo_inteiro(P_STATUS, STATUS_OK)
            + corpo
        )
        escritor.write(protobuf.quadro(mensagem))
        await escritor.drain()

    async def _esperar(self, numero: int) -> dict:
        """The next message of the television, refused when it is not the expected one."""
        leitor = self._leitor
        if leitor is None:
            raise _Falha(EQ_OFFLINE)
        async with asyncio.timeout(PASSO_S):
            while True:
                for corpo in protobuf.quadros(self._buffer):
                    campos = protobuf.ler(corpo)
                    status = protobuf.inteiro(campos, P_STATUS)
                    self._fio.recebido(f"status {status}")
                    if status != STATUS_OK:
                        raise _Falha(FALHOU)
                    if protobuf.tem(campos, numero):
                        return campos
                    raise _Falha(FALHOU)
                pedaco = await leitor.read(4096)
                if not pedaco:
                    raise _Falha(EQ_OFFLINE)
                self._buffer += pedaco


def _de_zero_a_cem(nivel: int, maximo: int) -> int | None:
    if maximo <= 0:
        return None
    return max(0, min(100, round(nivel * 100 / maximo)))


def _em_bytes(numero: int) -> bytes:
    """The number as the protocol hashes it: its hexadecimal, padded to whole bytes."""
    texto = f"{numero:X}"
    return bytes.fromhex(texto if len(texto) % 2 == 0 else f"0{texto}")


def _numeros_do_certificado(certificado: x509.Certificate) -> tuple[int, int]:
    numeros = certificado.public_key().public_numbers()
    return numeros.n, numeros.e


def _gerar_certificado() -> tuple[str, str]:
    """A key and a self signed certificate of this registration, in PEM.

    The television binds the pairing to this certificate, so it is generated once and kept
    as a secret of the registration; generating another one means pairing again.
    """
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NOME_DO_CLIENTE)])
    agora = datetime.now(UTC)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1))
        .not_valid_after(agora + timedelta(days=3650))
        # The television takes this certificate as the anchor of what it paired with, and a
        # leaf that is not allowed to sign itself is refused by some of them.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=False)
        .sign(chave, hashes.SHA256())
    )
    pem_do_certificado = certificado.public_bytes(serialization.Encoding.PEM).decode("ascii")
    pem_da_chave = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return pem_do_certificado, pem_da_chave


def _contexto_tls(certificado: str, chave: str) -> ssl.SSLContext:
    """A TLS context carrying our certificate, which both ports of the television ask for.

    The certificate of the television is self signed and there is nothing to verify it
    against, so verification is off and the identity is the pairing itself. The pair is
    written to a file of 0600 for as long as the loading takes, because the standard library
    loads a chain from a path and from nothing else, and the file is removed right after.
    """
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    descritor, caminho = tempfile.mkstemp(prefix="iphub-atv-", suffix=".pem")
    try:
        with os.fdopen(descritor, "w", encoding="ascii") as arquivo:
            arquivo.write(certificado)
            arquivo.write(chave)
        contexto.load_cert_chain(caminho)
    finally:
        with suppress(OSError):
            os.unlink(caminho)
    return contexto
