# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Sharp AQUOS televisions over the IP control of the television itself.

What the protocol is, so nobody has to read it again:

- One TCP connection on port 10002, and every command is EIGHT characters: four of the name
  and four of the parameter, padded with spaces, ended by a carriage return. `POWR1   ` turns
  it on and `POWR????` asks about it, and the answer is a short line, `OK`, `ERR`, or the
  value.
- Some models ask for a user and a password first, one line each, and answer OK to both. The
  two are fields of the registration and are sent when they are filled.
- The volume is a whole number to a ceiling of the model, sixty on most of them, which is a
  field of the registration because the television does not answer what its own is.
- Turning it on over the network only works on a model where Power On Command is enabled in
  the menu, which is what the text of this driver says to do first.
"""

import asyncio
import logging
import re

from iphub.config import Cadastro, ip_literal
from iphub.drivers import fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.sharp")

TIPO = "tv_sharp"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

PORTA = 10002
CONEXAO_S = 3.0
RESPOSTA_S = 2.0
LINHAS_ATE_DESISTIR = 4
FALHAS_ATE_OFFLINE = 2

FIM = b"\r"
LARGURA = 8
PERGUNTA = "????"
OK = "OK"
ERRO = "ERR"

CAMPO_USUARIO = "usuario"
CAMPO_SENHA = "senha"
CAMPO_VOLUME_MAXIMO = "volume_maximo"
VOLUME_MAXIMO_PADRAO = 60
VOLUME_MAXIMO_MINIMO = 2
VOLUME_MAXIMO_MAXIMO = 100

CMD_ENERGIA = "POWR"
CMD_VOLUME = "VOLM"
CMD_MUDO = "MUTE"
CMD_ENTRADA = "IAVD"
CMD_TV = "ITVD"
CMD_MODELO = "MODL"

MUDO_LIGADO = "1"
MUDO_DESLIGADO = "2"
ENTRADA_MINIMA = 1
ENTRADA_MAXIMA = 9
ENTRADA_DE_TV = "tv"

_SO_DIGITOS = re.compile(r"^[0-9]+$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TECLA = "tecla"

# The keys of this protocol are commands of their own, not a key code.
TECLAS = {
    "canal_mais": ("CHUP", "0"),
    "canal_menos": ("CHDW", "0"),
}

TEXTOS = {
    "en": {
        "descricao": (
            "Sharp AQUOS television over the IP control of the television, with no cloud and "
            "no account: power, volume, mute and the input."
        ),
        "preparo": (
            "On the television: Menu, Initial Setup, Network Setup, IP Control Setup. Enable "
            "IP control, note the port (10002 by default), and turn on the Power On Command, "
            "or the television only answers while it is already on."
        ),
        "campo_usuario": "Only on the models that ask for one before any command.",
        "campo_senha": "Only on the models that ask for one before any command.",
        "campo_volume_maximo": (
            "The volume ceiling of the model, 60 on most of them. The television does not "
            "answer what its own is."
        ),
        "cap_volume": (
            "Converted from the scale of the television, which counts to the ceiling above."
        ),
        "cap_fonte": "A number from 1 to 9 for an input, or the word tv for the tuner.",
    },
    "pt": {
        "descricao": (
            "Televisão Sharp AQUOS pelo controle IP da própria televisão, sem nuvem e sem "
            "conta: energia, volume, mudo e a entrada."
        ),
        "preparo": (
            "Na televisão: Menu, Configuração Inicial, Configuração de Rede, Configuração de "
            "Controle IP. Habilite o controle IP, anote a porta (10002 por padrão), e ligue o "
            "Power On Command, senão a televisão só responde enquanto já está ligada."
        ),
        "campo_usuario": "Só nos modelos que pedem um antes de qualquer comando.",
        "campo_senha": "Só nos modelos que pedem uma antes de qualquer comando.",
        "campo_volume_maximo": (
            "O teto de volume do modelo, 60 na maioria. A televisão não responde qual é o dela."
        ),
        "cap_volume": "Convertido da escala da televisão, que conta até o teto acima.",
        "cap_fonte": "Um número de 1 a 9 para uma entrada, ou a palavra tv para o sintonizador.",
    },
}

SUGESTOES = (
    Sugestao("entradas", "TV", ENTRADA_DE_TV),
    Sugestao("entradas", "HDMI 1", "1"),
    Sugestao("entradas", "HDMI 2", "2"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the television."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Sharp(Driver):
    """One Sharp AQUOS television, asked and commanded eight characters at a time."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Sharp AQUOS", "en": "Sharp AQUOS television"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TECLA,
        ),
        teclas=tuple(TECLAS),
        config_campos=(
            Campo(nome=CAMPO_USUARIO, tipo=TipoCampo.TEXTO, obrigatorio=False),
            Campo(nome=CAMPO_SENHA, tipo=TipoCampo.SEGREDO, obrigatorio=False),
            Campo(
                nome=CAMPO_VOLUME_MAXIMO,
                tipo=TipoCampo.INTEIRO,
                padrao=str(VOLUME_MAXIMO_PADRAO),
            ),
        ),
        # The television announces nothing this hub can claim, so it is found by the range
        # sweep, which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Sharp",
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
            await self._mandar(CMD_ENERGIA, "1")
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._mandar(CMD_ENERGIA, "0")
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._mandar(CMD_VOLUME, str(self._para_o_aparelho(valor)))
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._mandar(CMD_MUDO, MUDO_LIGADO if valor else MUDO_DESLIGADO)
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            entrada = _entrada_valida(valor)
            if entrada is None:
                return INVALID_VALUE
            if entrada == ENTRADA_DE_TV:
                await self._mandar(CMD_TV, "0")
            else:
                await self._mandar(CMD_ENTRADA, entrada)
            self._defina(fonte=entrada)
            return None
        if acao == ACAO_TECLA:
            tecla = TECLAS.get(valor) if isinstance(valor, str) else None
            if tecla is None:
                return INVALID_VALUE
            await self._mandar(*tecla)
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: power first, because a television that is off answers nothing else."""
        energia = await self._perguntar(CMD_ENERGIA)
        ligado = energia == "1"
        if not ligado:
            self._defina(online=True, ligado=False, detalhe="")
            return
        volume = await self._perguntar(CMD_VOLUME)
        mudo = await self._perguntar(CMD_MUDO)
        entrada = await self._perguntar(CMD_ENTRADA)
        self._defina(
            online=True,
            ligado=True,
            volume=self._do_aparelho(volume),
            mudo=None if not mudo else mudo == MUDO_LIGADO,
            # A television on the tuner answers the input question with an error, which is
            # how this protocol says the tuner is what is in front.
            fonte=entrada if _SO_DIGITOS.match(entrada) else ENTRADA_DE_TV,
            detalhe="",
        )

    async def _perguntar(self, comando: str) -> str:
        resposta = await self._trocar(comando, PERGUNTA, rotina=True)
        return "" if resposta in (ERRO, OK) else resposta

    async def _mandar(self, comando: str, parametro: str) -> None:
        resposta = await self._trocar(comando, parametro)
        if resposta == ERRO:
            # The television refused the value, which is not a television that broke.
            self._fio.recusado(comando, INVALID_VALUE)
            raise _Falha(INVALID_VALUE)

    async def _trocar(self, comando: str, parametro: str, *, rotina: bool = False) -> str:
        leitor, escritor = await self._conectar()
        quadro = f"{comando}{parametro}".ljust(LARGURA)
        self._fio.enviado(quadro, rotina=rotina)
        try:
            escritor.write(quadro.encode("ascii") + FIM)
            await escritor.drain()
        except (OSError, RuntimeError) as erro:
            self._fio.falhou(quadro, erro)
            raise _Falha(EQ_OFFLINE) from erro
        for _ in range(LINHAS_ATE_DESISTIR):
            linha = await self._ler_linha()
            self._fio.recebido(linha, rotina=rotina)
            if linha:
                return linha
        raise _Falha(ERRO_APARELHO)

    async def _ler_linha(self) -> str:
        leitor, _escritor = await self._conectar()
        try:
            async with asyncio.timeout(RESPOSTA_S):
                bruto = await leitor.readuntil(FIM)
        except TimeoutError:
            self._fio.falhou("read", "no answer")
            raise _Falha(ERRO_APARELHO) from None
        except (OSError, asyncio.IncompleteReadError, ValueError) as erro:
            self._fio.falhou("read", erro)
            raise _Falha(EQ_OFFLINE) from erro
        return bruto.decode("ascii", errors="replace").strip()

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
        await self._entrar(leitor, escritor)
        self._fio.reconectou()
        return leitor, escritor

    async def _entrar(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        """The user and the password of the models that ask for them, one line each."""
        usuario = self.cadastro.campos.get(CAMPO_USUARIO, "").strip()
        senha = self.cadastro.segredos.get(CAMPO_SENHA, "").strip()
        if not usuario and not senha:
            return
        # The credential never reaches the transcript: the log of this hub carries what was
        # asked, never what proves who is asking.
        self._fio.enviado("login")
        try:
            escritor.write(usuario.encode("ascii") + FIM + senha.encode("ascii") + FIM)
            await escritor.drain()
        except (OSError, RuntimeError, UnicodeEncodeError) as erro:
            self._fio.falhou("login", erro)
            raise _Falha(EQ_OFFLINE) from erro
        for _ in range(2):
            try:
                async with asyncio.timeout(RESPOSTA_S):
                    await leitor.readuntil(FIM)
            except (TimeoutError, OSError, asyncio.IncompleteReadError, ValueError):
                return

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


def _entrada_valida(valor: object) -> str | None:
    """An input of this television is a number from one to nine, or the word of the tuner."""
    if not isinstance(valor, str):
        return None
    limpo = valor.strip().lower()
    if limpo == ENTRADA_DE_TV:
        return ENTRADA_DE_TV
    if not _SO_DIGITOS.match(limpo):
        return None
    numero = int(limpo)
    return str(numero) if ENTRADA_MINIMA <= numero <= ENTRADA_MAXIMA else None
