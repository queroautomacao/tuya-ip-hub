# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""PJLink class 1 over TCP, the example native driver of section 6.

The protocol is published, it needs a real pairing flow and it has no volume, so it
exercises the contract without pretending to support what it cannot. The device greets with
"PJLINK 0" (security off) or "PJLINK 1 <seed>" (security on), and the first command of a
connection carries MD5(seed + password) in front of it. Every exchange has its own
connection with a deadline: a class 1 device usually accepts one connection at a time, and
holding one would lock out the remote control of the room.

PJLink classe 1 sobre TCP, o driver nativo de exemplo da seção 6.

O protocolo é publicado, ele precisa de um fluxo real de pareamento e não tem volume, então
exercita o contrato sem fingir suportar o que não pode. O aparelho sauda com "PJLINK 0"
(segurança desligada) ou "PJLINK 1 <semente>" (segurança ligada), e o primeiro comando de
uma conexão leva MD5(semente + senha) na frente. Cada troca tem conexão própria com prazo:
um aparelho de classe 1 costuma aceitar uma conexão por vez, e segurar uma trancaria o
controle remoto da sala.
"""

import asyncio
import hashlib
import re
from contextlib import suppress

from iphub.drivers.base import PAREADO, Cadastro, Driver
from iphub.drivers.manifesto import Auth, Campo, Manifesto, TipoCampo

PORTA_PADRAO = 4352
TEMPO_LIMITE_S = 4.0
# Why: one poll asks up to four questions, so a deadline that is only per exchange lets a
# projector that greets and then goes silent hold its slot for four times the limit.
# Por que: um poll faz até quatro perguntas, então um prazo só por troca deixa um projetor
# que sauda e depois cala segurar sua vaga por quatro vezes o limite.
ORCAMENTO_DO_POLL_S = 6.0
# Why: a device on the LAN must not be able to make the daemon buffer without bound, so an
# answer that never terminates is cut long before it costs memory.
# Por que: um aparelho na LAN não pode fazer o daemon acumular sem limite, então uma
# resposta que nunca termina é cortada muito antes de custar memória.
LINHA_MAXIMA = 1024

TERMINADOR = "\r"
SAUDACAO = "PJLINK"
SEM_SEGURANCA = "0"
COM_SEGURANCA = "1"
ERRO_DE_SENHA = "ERRA"
CABECALHO = 6
OK = "OK"

CAMPO_PORTA = "porta"
CAMPO_SENHA = "senha"

_PORTA = re.compile(r"[0-9]{1,5}")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_FONTE = "fonte"
ACAO_MUDO = "mudo"

LIGAR = "%1POWR 1"
DESLIGAR = "%1POWR 0"
PERGUNTA_ENERGIA = "%1POWR ?"
PERGUNTA_ENTRADA = "%1INPT ?"
PERGUNTA_ENTRADAS = "%1INST ?"
PERGUNTA_MUDO = "%1AVMT ?"
TROCA_ENTRADA = "%1INPT "
MUDO_LIGADO = "%1AVMT 21"
MUDO_DESLIGADO = "%1AVMT 20"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
AUTH_PENDENTE = "auth_pendente"
ERRO_APARELHO = "erro_aparelho"
FALHOU = "falhou"

CODIGO_POR_ERRO = {
    "ERR1": INVALID_VALUE,
    "ERR2": INVALID_VALUE,
    "ERR3": EQ_OFFLINE,
    "ERR4": ERRO_APARELHO,
}

# Why: warm up is on its way to lit and cool down is on its way to dark, so each one reports
# where the projector is heading; the panel would otherwise show a lamp that flickers.
# Por que: aquecendo está a caminho de aceso e resfriando a caminho de apagado, então cada um
# reporta para onde o projetor vai; o painel mostraria uma lâmpada piscando.
LIGADO_POR_ENERGIA = {"0": False, "1": True, "2": False, "3": True}

# The mute of section 6 is the audio one, so 21 and 20 are written and the picture is never
# blanked; 11 is a blank picture with the sound on, and 31 blanks both, so it reads as muted.
# O mudo da seção 6 é o de áudio, então 21 e 20 são escritos e a imagem nunca é apagada; 11 é
# imagem apagada com o som ligado, e 31 apaga as duas, então é lido como mudo.
MUDO_POR_CODIGO = {"10": False, "11": False, "20": False, "21": True, "30": False, "31": True}

TEXTOS = {
    "en": {
        "descricao": "PJLink class 1 over TCP: power, input and audio mute, picture stays on.",
        "campo_porta": "TCP port of the projector, 4352 unless it was changed.",
        "campo_senha": "PJLink password, only when the projector has security on.",
        "auth_ajuda": (
            "Turn PJLink security on at the projector, type the same password here and pair. "
            "The projector shows no popup: the answer comes from the connection itself."
        ),
    },
    "pt": {
        "descricao": "PJLink classe 1 por TCP: energia, entrada e mudo de áudio, imagem fica.",
        "campo_porta": "Porta TCP do projetor, 4352 a menos que tenha sido trocada.",
        "campo_senha": "Senha PJLink, só quando o projetor está com segurança ligada.",
        "auth_ajuda": (
            "Ligue a segurança PJLink no projetor, digite a mesma senha aqui e pareie. "
            "O projetor não mostra popup: a resposta vem da própria conexão."
        ),
    },
}


class _Falha(Exception):
    """A stable code on the way out of an exchange, so no exception escapes executar.

    autenticado says the device answered PJLink for our command, which means the credential
    was accepted even when the answer itself was an error.

    Um código estável na saída de uma troca, para nenhuma exceção escapar do executar.

    autenticado diz que o aparelho respondeu PJLink ao nosso comando, o que significa que a
    credencial foi aceita mesmo quando a resposta em si foi um erro.
    """

    def __init__(self, codigo: str, *, autenticado: bool = False) -> None:
        self.codigo = codigo
        self.autenticado = autenticado
        super().__init__(codigo)


class PJLink(Driver):
    """Power, input and mute of a class 1 projector, and nothing it cannot do.

    Energia, entrada e mudo de um projetor classe 1, e nada que ele não saiba fazer.
    """

    # Why: class 1 has no volume command, and section 6 says the right move is to omit the
    # capability, never to implement a method that only refuses.
    # Por que: a classe 1 não tem comando de volume, e a seção 6 diz que o certo é omitir a
    # capacidade, nunca implementar um método que só recusa.
    MANIFESTO = Manifesto(
        tipo="projetor_pjlink",
        rotulo={"pt": "Projetor PJLink", "en": "PJLink projector"},
        categoria="projetor",
        capacidades=(ACAO_LIGAR, ACAO_DESLIGAR, ACAO_FONTE, ACAO_MUDO),
        auth=Auth.CODIGO,
        # Discovery stays empty on purpose: class 1 announces nothing on the segment, and the
        # class 2 search is another transport, which arrives with the driver that needs it.
        # A descoberta fica vazia de propósito: a classe 1 não anuncia nada no segmento, e a
        # busca da classe 2 é outro transporte, que chega com o driver que precisar dele.
        config_campos=(
            Campo(CAMPO_PORTA, TipoCampo.INTEIRO, padrao=str(PORTA_PADRAO)),
            Campo(CAMPO_SENHA, TipoCampo.SEGREDO),
        ),
        textos=TEXTOS,
        motor="nativo",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._porta = porta_de(cadastro.campos)

    async def autenticar(self) -> str:
        """Section 6: pairing is explicit. This protocol has no popup, so it never waits.

        Seção 6: o pareamento é explícito. Este protocolo não tem popup, então nunca aguarda.
        """
        try:
            await self._falar(PERGUNTA_ENERGIA)
        except _Falha as falha:
            return PAREADO if falha.autenticado else FALHOU
        return PAREADO

    async def atualizar(self) -> None:
        prazo_final = asyncio.get_running_loop().time() + ORCAMENTO_DO_POLL_S
        try:
            energia = await self._falar(PERGUNTA_ENERGIA, prazo_final)
        except _Falha as falha:
            self._defina(online=False, detalhe=falha.codigo)
            return
        ligado = LIGADO_POR_ENERGIA.get(energia)
        self._defina(online=True, ligado=ligado, detalhe="")
        # Why: a projector that is off answers ERR3 to everything else, and reading that as a
        # fault would flip a healthy device to offline on every poll.
        # Por que: um projetor apagado responde ERR3 a todo o resto, e ler isso como falha
        # jogaria um aparelho saudável para offline a cada poll.
        if not ligado:
            return
        if not self.estado().fontes:
            with suppress(_Falha):
                self._defina(fontes=entradas_de(await self._falar(PERGUNTA_ENTRADAS, prazo_final)))
        with suppress(_Falha):
            self._defina(fonte=_entrada_lida(await self._falar(PERGUNTA_ENTRADA, prazo_final)))
        with suppress(_Falha):
            self._defina(mudo=MUDO_POR_CODIGO.get(await self._falar(PERGUNTA_MUDO, prazo_final)))

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao in (ACAO_LIGAR, ACAO_DESLIGAR):
            ligar = acao == ACAO_LIGAR
            await self._mandar(LIGAR if ligar else DESLIGAR)
            self._defina(ligado=ligar)
            return None
        if acao == ACAO_FONTE:
            return await self._trocar_fonte(valor)
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._mandar(MUDO_LIGADO if valor else MUDO_DESLIGADO)
            self._defina(mudo=valor)
            return None
        return await super().executar(acao, valor)

    async def _trocar_fonte(self, valor: object) -> str | None:
        # Why: the value decides bytes on a socket, so it is checked before the connection is
        # even opened; a value carrying a terminator would be a second command on the wire.
        # Por que: o valor decide bytes num socket, então é conferido antes de a conexão ser
        # aberta; um valor levando um terminador seria um segundo comando no fio.
        fontes = self.estado().fontes
        if not entrada_valida(valor) or (fontes and valor not in fontes):
            return INVALID_VALUE
        entrada = str(valor)
        await self._mandar(TROCA_ENTRADA + entrada)
        self._defina(fonte=entrada)
        return None

    async def _mandar(self, comando: str) -> None:
        if (await self._falar(comando)).upper() != OK:
            raise _Falha(ERRO_APARELHO, autenticado=True)

    async def _falar(self, comando: str, prazo_final: float | None = None) -> str:
        """One connection, one command, and the value the device answered after the sign.

        prazo_final is the deadline of the whole poll, shared by its exchanges.

        Uma conexão, um comando, e o valor que o aparelho respondeu depois do sinal.

        prazo_final é o prazo do poll inteiro, dividido entre as trocas dele.
        """
        endereco = self.cadastro.ip
        # Why: an empty address would let the resolver aim the command at the loopback, which
        # is the daemon itself; the hub never talks to a host nobody registered.
        # Por que: um endereço vazio deixaria o resolvedor apontar o comando para o loopback,
        # que é o próprio daemon; o hub nunca fala com um host que ninguém cadastrou.
        if not endereco:
            raise _Falha(EQ_OFFLINE)
        limite = TEMPO_LIMITE_S
        if prazo_final is not None:
            limite = min(limite, prazo_final - asyncio.get_running_loop().time())
        # Why: a spent budget drops the questions the poll has left, before a socket is opened.
        # Por que: um orçamento gasto derruba as perguntas que faltam ao poll, antes do socket.
        if limite <= 0:
            raise _Falha(EQ_OFFLINE)
        try:
            async with asyncio.timeout(limite):
                leitor, escritor = await asyncio.open_connection(
                    endereco, self._porta, limit=LINHA_MAXIMA
                )
                try:
                    prefixo = self._prefixo(await _linha(leitor))
                    escritor.write(f"{prefixo}{comando}{TERMINADOR}".encode("ascii"))
                    await escritor.drain()
                    return _valor(comando, await _linha(leitor))
                finally:
                    await _fechar(escritor)
        except (
            TimeoutError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as erro:
            raise _Falha(EQ_OFFLINE) from erro

    def _prefixo(self, saudacao: str) -> str:
        partes = saudacao.split()
        if len(partes) == 2 and partes[0] == SAUDACAO:
            if partes[1] == SEM_SEGURANCA:
                return ""
            if partes[1] == ERRO_DE_SENHA:
                raise _Falha(AUTH_PENDENTE)
        if len(partes) == 3 and partes[0] == SAUDACAO and partes[1] == COM_SEGURANCA:
            if not _e_hexadecimal(partes[2]):
                raise _Falha(ERRO_APARELHO)
            return _digesto(partes[2], self.cadastro.segredos.get(CAMPO_SENHA, ""))
        raise _Falha(ERRO_APARELHO)


def porta_de(campos: dict[str, str]) -> int:
    """The registered port, or the published default when the field is absent or unusable.

    A porta cadastrada, ou o padrão publicado quando o campo falta ou não serve.
    """
    bruto = str(campos.get(CAMPO_PORTA, "")).strip()
    # Why: str.isdigit is true for a superscript and for every other Unicode digit set, which
    # int then refuses, raising inside the registration, or reads as a port nobody asked for.
    # Por que: str.isdigit é verdadeiro para sobrescrito e todo outro conjunto de dígitos
    # Unicode, que o int então recusa, estourando no cadastro, ou lê como porta que ninguém pediu.
    if _PORTA.fullmatch(bruto) and 1 <= int(bruto) <= 65535:
        return int(bruto)
    return PORTA_PADRAO


def entrada_valida(valor: object) -> bool:
    """An input of class 1 is two digits: a kind of 1 to 5 and a number of 1 to 9.

    Uma entrada da classe 1 tem dois dígitos: um tipo de 1 a 5 e um número de 1 a 9.
    """
    return (
        isinstance(valor, str)
        and len(valor) == 2
        and valor[0] in "12345"
        and valor[1] in "123456789"
    )


def entradas_de(valor: str) -> tuple[str, ...]:
    """The inputs the projector says it has, keeping only what the protocol allows.

    As entradas que o projetor diz ter, guardando só o que o protocolo permite.
    """
    return tuple(item for item in valor.split() if entrada_valida(item))


def _entrada_lida(valor: str) -> str:
    """An answer to the input question that is not an input of the protocol is refused.

    A device answers what it likes, and that is what lands in the state, in the API answer and
    on the panel screen when the driver copies it; the list of inputs beside it is filtered.

    Uma resposta à pergunta de entrada que não é uma entrada do protocolo é recusada.

    Um aparelho responde o que quiser, e é isso que cai no estado, na resposta da API e na tela
    do painel quando o driver copia; a lista de entradas ao lado é filtrada.
    """
    if not entrada_valida(valor):
        raise _Falha(ERRO_APARELHO)
    return valor


def _digesto(semente: str, senha: str) -> str:
    # Why: this md5 is the PJLink handshake and not a secret at rest, and saying so keeps the
    # daemon alive on a build whose hash policy refuses md5 by default.
    # Por que: este md5 é o aperto de mão do PJLink e não um segredo em repouso, e dizê-lo
    # mantém o daemon vivo num build cuja política de hash recusa md5 por padrão.
    bruto = f"{semente}{senha}".encode()
    return hashlib.md5(bruto, usedforsecurity=False).hexdigest()


def _e_hexadecimal(texto: str) -> bool:
    return bool(texto) and all(c in "0123456789abcdefABCDEF" for c in texto)


def _valor(comando: str, resposta: str) -> str:
    if resposta.split() == [SAUDACAO, ERRO_DE_SENHA]:
        raise _Falha(AUTH_PENDENTE)
    cabeca, separador, valor = resposta.partition("=")
    # Why: a device answering another command is out of step with us, and nothing it says can
    # be trusted for this exchange.
    # Por que: um aparelho respondendo outro comando está fora de passo conosco, e nada do
    # que ele diz serve para esta troca.
    if not separador or cabeca != comando[:CABECALHO]:
        raise _Falha(ERRO_APARELHO)
    codigo = CODIGO_POR_ERRO.get(valor.upper())
    if codigo:
        raise _Falha(codigo, autenticado=True)
    return valor


async def _linha(leitor: asyncio.StreamReader) -> str:
    bruto = await leitor.readuntil(TERMINADOR.encode("ascii"))
    return bruto[: -len(TERMINADOR)].decode("ascii", errors="replace").strip()


async def _fechar(escritor: asyncio.StreamWriter) -> None:
    escritor.close()
    # Why: a cancellation of the poll must reach the caller, so only the noise of a peer
    # that already went away is swallowed here.
    # Por que: um cancelamento do poll precisa chegar a quem chamou, então só o ruído de um
    # par que já foi embora é engolido aqui.
    with suppress(OSError, TimeoutError):
        await escritor.wait_closed()
