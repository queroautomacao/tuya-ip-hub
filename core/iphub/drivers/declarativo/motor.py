# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7: a Driver of section 6 built from a declaration, so nothing above knows the
difference.

A declarative driver is a Driver like any other: the gestor, the panel and the discovery see
a manifest, a lifecycle, a typed Estado and one stable code back. The declaration is DATA:
this module reads it, it never runs it. The only substitutions inside the text of a command
are {valor}, {valor_escala} and {ip}, applied in one pass so a value that carries a brace is
never expanded again, and a hexadecimal literal takes none of them because it is bytes.

Nothing raises out of executar or atualizar: every failure becomes one of the stable codes.

Seção 7: um Driver da seção 6 montado de uma declaração, para nada acima notar a diferença.

Um driver declarativo é um Driver como outro qualquer: o gestor, o painel e a descoberta veem
um manifesto, um ciclo de vida, um Estado tipado e um código estável de volta. A declaração é
DADO: este módulo a lê, nunca a executa. As únicas substituições dentro do texto de um comando
são {valor}, {valor_escala} e {ip}, aplicadas numa passada para um valor que leve uma chave
nunca ser expandido de novo, e um literal hexadecimal não leva nenhuma porque é byte.

Nada estoura para fora do executar ou do atualizar: toda falha vira um dos códigos estáveis.
"""

import json
import logging
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from iphub import regex_seguro
from iphub.drivers.base import NAO_SUPORTADO, PAREADO, Cadastro, Driver
from iphub.drivers.declarativo.formato import (
    BOOLEANAS,
    INTEIRAS,
    LEITURAS,
    Consulta,
    Definicao,
    Escala,
    Leitura,
    Passo,
)
from iphub.drivers.declarativo.transporte import ERRO_APARELHO, FalhaDeTransporte, canal_de
from iphub.drivers.manifesto import TEMPERATURA_MAXIMA, TEMPERATURA_MINIMA, Auth

log = logging.getLogger("iphub.drivers.declarativo.motor")

INVALID_VALUE = "invalid_value"
FALHOU = "falhou"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TEMPERATURA = "temperatura"
ACAO_MODO = "modo"
ACAO_VENTO = "vento"

CAMPO_VOLUME = "volume"
CAMPO_FONTE = "fonte"
CAMPO_TEMPERATURA = "temperatura"

# The readings whose wire value is translated back through the values map of a command, so
# the panel reads the label it offered and the numbers module reads the word of section 6.
# As leituras cujo valor de fio é traduzido de volta pelo mapa de valores de um comando, para
# o painel ler o rótulo que ofereceu e o módulo dos números ler a palavra da seção 6.
ROTULO_DO_COMANDO = {CAMPO_FONTE: ACAO_FONTE, ACAO_MODO: ACAO_MODO, ACAO_VENTO: ACAO_VENTO}

VERDADE = "true"
FALSIDADE = "false"

CONTRATO_MINIMO = 0
CONTRATO_MAXIMO = 100

# Why: a value chosen in the panel becomes bytes on a wire, so it is cut before it gets
# there; a device answers what it likes, and the same ceiling holds for what is read back.
# Por que: um valor escolhido no painel vira bytes num fio, então é cortado antes de chegar
# lá; um aparelho responde o que quiser, e o mesmo teto vale para o que é lido de volta.
VALOR_MAXIMO = 200

# The three substitutions, and nothing else: the format is data and has no expression in it.
# As três substituições, e nada mais: o formato é dado e não tem expressão nenhuma.
_MARCADORES = re.compile(r"\{(valor|valor_escala|ip)\}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


class Buscador(Protocol):
    """The safe regex of section 7: a read never calls `re` on this side of the process.

    A regex segura da seção 7: uma leitura nunca chama o `re` deste lado do processo.
    """

    async def buscar_async(self, padrao: str, texto: str) -> list[str | None] | None: ...


class _Recusa(Exception):
    """A stable code on the way out of a command, so no exception escapes executar.

    Um código estável na saída de um comando, para nenhuma exceção escapar do executar.
    """

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class DriverDeclarativo(Driver):
    """The engine of section 7 as one Driver; construir makes the class of a declaration.

    O motor da seção 7 como um Driver; o construir faz a classe de uma declaração.
    """

    DEFINICAO: Definicao
    REGEX: Buscador | None = None

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._canal = canal_de(self.DEFINICAO.transporte, cadastro)
        self._regex = self.REGEX if self.REGEX is not None else regex_seguro.instancia()
        # Why: the sources are the keys of the fonte map, which is the file itself; the panel
        # gets them before the first poll instead of showing an empty list on a fresh cadastro.
        # Por que: as fontes são as chaves do mapa de fonte, que é o próprio arquivo; o painel
        # as recebe antes do primeiro poll em vez de mostrar lista vazia num cadastro novo.
        self._defina(fontes=self.DEFINICAO.fontes)

    async def iniciar(self) -> None:
        await self._canal.abrir()

    async def parar(self) -> None:
        await self._canal.fechar()

    async def autenticar(self) -> str:
        """A declaration has no handshake to describe, so what pairing proves here is that the
        credential of the cadastro reaches the device: the state question is what asks it.

        Uma declaração não tem aperto de mão para descrever, então o que o pareamento prova
        aqui é que a credencial do cadastro alcança o aparelho: a pergunta de estado é quem
        pergunta isso.
        """
        if self.MANIFESTO.auth is Auth.NENHUMA:
            return await super().autenticar()
        consulta = self.DEFINICAO.estado
        if consulta is None:
            # Why: with nothing to ask, nothing was proven, and answering "pareado" would tell
            # the panel a credential works while every command still fails.
            # Por que: sem nada a perguntar, nada foi provado, e responder "pareado" diria ao
            # painel que uma credencial funciona enquanto todo comando falha.
            return FALHOU
        try:
            await self._canal.perguntar(self._passos_do_estado(consulta))
        except (FalhaDeTransporte, _Recusa):
            return FALHOU
        return PAREADO

    async def atualizar(self) -> None:
        try:
            await self._perguntar()
        except (FalhaDeTransporte, _Recusa) as falha:
            self._defina(online=False, detalhe=falha.codigo)
        except Exception as erro:
            log.exception("the declarative poll of %s failed: %s", self.MANIFESTO.tipo, erro)
            self._defina(online=False, detalhe=ERRO_APARELHO)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except (FalhaDeTransporte, _Recusa) as falha:
            return falha.codigo
        except Exception as erro:
            log.exception(
                "the declarative command %r of %s failed: %s", acao, self.MANIFESTO.tipo, erro
            )
            return ERRO_APARELHO

    async def _agir(self, acao: str, valor: object) -> str | None:
        comando = self.DEFINICAO.comandos.get(acao)
        # Why: the gestor already refused what the manifest does not declare, and this is the
        # same answer for a driver driven straight, with no gestor above it.
        # Por que: o gestor já recusou o que o manifesto não declara, e esta é a mesma resposta
        # para um driver dirigido direto, sem gestor acima.
        if comando is None or acao not in self.MANIFESTO.capacidades:
            return NAO_SUPORTADO
        substituicoes = self._substituicoes(acao, comando.valores, valor)
        passos = _renderizar(comando.passos, substituicoes, comando.repete)
        await self._canal.enviar(passos, intervalo_ms=comando.intervalo_ms)
        self._defina(**_otimismo(acao, valor))
        return None

    def _substituicoes(self, acao: str, valores: dict[str, str], valor: object) -> dict[str, str]:
        """What {valor}, {valor_escala} and {ip} stand for in this one command.

        O que {valor}, {valor_escala} e {ip} significam neste comando.
        """
        substituicoes = {"ip": self.cadastro.ip}
        if acao == ACAO_VOLUME:
            # Why: section 6 fixes the contract at 0 to 100, so the scale of the device is
            # converted here and a value outside the contract never reaches the wire.
            # Por que: a seção 6 fixa o contrato em 0 a 100, então a escala do aparelho é
            # convertida aqui e um valor fora do contrato nunca chega ao fio.
            if type(valor) is not int or not CONTRATO_MINIMO <= valor <= CONTRATO_MAXIMO:
                raise _Recusa(INVALID_VALUE)
            substituicoes["valor_escala"] = str(escalar_para_aparelho(valor, self._escala()))
        if acao == ACAO_TEMPERATURA and (
            type(valor) is not int or not TEMPERATURA_MINIMA <= valor <= TEMPERATURA_MAXIMA
        ):
            # Why: section 6 fixes the setpoint in whole degrees of the range, and a value
            # outside it never reaches the wire of a compressor.
            # Por que: a seção 6 fixa o setpoint em graus inteiros da faixa, e um valor fora
            # dela nunca chega ao fio de um compressor.
            raise _Recusa(INVALID_VALUE)
        chave = _chave(valor)
        if valores:
            # Why: the map is the whole vocabulary the file gave this action, so a value
            # outside it is refused instead of being written raw onto the wire.
            # Por que: o mapa é todo o vocabulário que o arquivo deu a esta ação, então um
            # valor fora dele é recusado em vez de ser escrito cru no fio.
            if chave is None or chave not in valores:
                raise _Recusa(INVALID_VALUE)
            substituicoes["valor"] = _limpo(valores[chave])
        elif chave is not None:
            substituicoes["valor"] = _limpo(chave)
        return substituicoes

    async def _perguntar(self) -> None:
        consulta = self.DEFINICAO.estado
        if consulta is None:
            # Why: a file with no estado block gives the hub nothing to ask, and reporting a
            # device offline for that would hide every fire and forget equipment behind a
            # false alarm; a command that fails still answers with its own code.
            # Por que: um arquivo sem bloco estado não dá ao hub o que perguntar, e reportar o
            # aparelho offline por isso esconderia todo equipamento de mão única atrás de um
            # alarme falso; um comando que falha ainda responde com o código dele.
            self._defina(online=True, detalhe="")
            return
        respostas = await self._canal.perguntar(self._passos_do_estado(consulta))
        self._defina(online=True, detalhe="", **await self._ler(consulta.le, respostas))

    def _passos_do_estado(self, consulta: Consulta) -> tuple[Passo, ...]:
        return _renderizar(consulta.pede, {"ip": self.cadastro.ip}, 1)

    async def _ler(
        self, leituras: Sequence[Leitura], respostas: Sequence[str]
    ) -> dict[str, object]:
        """Every reading against every answer, in order, and the first value found wins.

        Section 7 asks state in more than one request, and which answer carries which field is
        not something the file says, so trying them in order is a rule of the engine and never
        a condition written in the file.

        Toda leitura contra toda resposta, na ordem, e o primeiro valor achado vence.

        A seção 7 pede estado em mais de uma requisição, e qual resposta leva qual campo não é
        algo que o arquivo diga, então tentar na ordem é regra do motor e nunca condicional
        escrita no arquivo.
        """
        documentos: dict[int, object] = {}
        campos: dict[str, object] = {}
        for leitura in leituras:
            if leitura.campo not in LEITURAS:
                continue
            for indice, resposta in enumerate(respostas):
                bruto = await self._extrair(leitura, indice, resposta, documentos)
                valor = None if bruto is None else self._converter(leitura, bruto)
                if valor is not None:
                    campos[leitura.campo] = valor
                    break
        return campos

    async def _extrair(
        self, leitura: Leitura, indice: int, resposta: str, documentos: dict[int, object]
    ) -> object:
        if leitura.caminho:
            if indice not in documentos:
                documentos[indice] = _documento(resposta)
            return _no_caminho(documentos[indice], leitura.caminho)
        grupos = await self._regex.buscar_async(leitura.regex, resposta)
        if grupos is None:
            # Why: None is a deadline blown or a pattern `re` refused, which is a defect of the
            # driver and not of the device; the read is dropped and the poll goes on.
            # Por que: None é prazo estourado ou padrão que o `re` recusou, que é defeito do
            # driver e não do aparelho; a leitura cai e o poll segue.
            log.warning("the reading of %s did not answer: %r", leitura.campo, leitura.regex)
            return None
        return grupos[0] if grupos else None

    def _converter(self, leitura: Leitura, bruto: object) -> object:
        if leitura.campo in BOOLEANAS:
            return _verdade(bruto, leitura.verdadeiro)
        if leitura.campo == CAMPO_VOLUME:
            return _volume_lido(bruto, self._escala())
        if leitura.campo in INTEIRAS:
            return _inteiro_lido(bruto)
        texto = _limpo(str(bruto))
        if leitura.campo in ROTULO_DO_COMANDO:
            return self._rotulo_lido(ROTULO_DO_COMANDO[leitura.campo], texto)
        return texto

    def _rotulo_lido(self, acao: str, fio: str) -> str:
        """The wire value read back becomes the label the command offered (the label of an
        input, the word of section 6 of a mode), or it means nothing.

        O valor de fio lido de volta vira o rótulo que o comando ofereceu (o rótulo de uma
        entrada, a palavra da seção 6 de um modo), ou não significa nada.
        """
        comando = self.DEFINICAO.comandos.get(acao)
        valores = comando.valores if comando is not None else {}
        for rotulo, valor in valores.items():
            if valor == fio:
                return rotulo
        return fio

    def _escala(self) -> Escala | None:
        return self.DEFINICAO.escala


def construir(definicao: Definicao, *, regex: Buscador | None = None) -> type[Driver]:
    """The class of one declaration: a Driver the catalog and the gestor treat like a native.

    A classe de uma declaração: um Driver que o catálogo e o gestor tratam como um nativo.
    """
    manifesto = definicao.manifesto
    corpo = {
        "MANIFESTO": manifesto,
        "DEFINICAO": definicao,
        "REGEX": regex,
        "__doc__": (
            f"{manifesto.rotulo['en']} ({manifesto.rotulo['pt']}), section 7 as data.\n\n"
            f"{manifesto.rotulo['pt']}, a seção 7 como dado.\n"
        ),
    }
    return type(f"Declarativo_{manifesto.tipo}", (DriverDeclarativo,), corpo)


def escalar_para_aparelho(valor: int, escala: Escala | None) -> int:
    """The 0 to 100 of the contract in the range the device speaks.

    O 0 a 100 do contrato na faixa que o aparelho fala.
    """
    if escala is None:
        return valor
    largura = escala.maximo - escala.minimo
    return escala.minimo + round(valor * largura / CONTRATO_MAXIMO)


def escalar_para_contrato(bruto: float, escala: Escala | None) -> int | None:
    """What the device answered in the 0 to 100 section 6 fixes, clamped to it, or None when
    what it answered is not a finite number.

    O que o aparelho respondeu no 0 a 100 que a seção 6 fixa, preso a ele, ou None quando o
    que ele respondeu não é um número finito.
    """
    if not math.isfinite(bruto):
        # Why: infinity and NaN are not volumes, and converting them raised OverflowError and
        # ValueError inside the poll, which threw away every OTHER field read in that same
        # poll and pinned a powered device offline until someone restarted the daemon.
        # Por que: infinito e NaN não são volume, e convertê-los estourava OverflowError e
        # ValueError dentro do poll, o que jogava fora todo campo lido no MESMO poll e
        # prendia offline um aparelho ligado até alguém reiniciar o daemon.
        return None
    minimo = escala.minimo if escala is not None else CONTRATO_MINIMO
    maximo = escala.maximo if escala is not None else CONTRATO_MAXIMO
    # Why: a finite number the size of 1e308 overflows to infinity in the multiplication
    # below, so what the device answered is held inside its own range first; the answer is
    # the same for everything that was already in range, because the conversion only grows.
    # Por que: um número finito do tamanho de 1e308 transborda para infinito na multiplicação
    # abaixo, então o que o aparelho respondeu é preso à faixa dele antes; a resposta é a
    # mesma para tudo que já estava na faixa, porque a conversão só cresce.
    preso = max(float(minimo), min(float(maximo), bruto))
    if escala is None:
        return round(preso)
    largura = escala.maximo - escala.minimo
    convertido = (preso - escala.minimo) * CONTRATO_MAXIMO / largura
    return max(CONTRATO_MINIMO, min(CONTRATO_MAXIMO, round(convertido)))


def _renderizar(
    passos: Sequence[Passo], substituicoes: dict[str, str], repete: int
) -> tuple[Passo, ...]:
    """The steps with the substitutions applied, the declared repetition of the whole sequence.

    Os passos com as substituições aplicadas, a repetição declarada da sequência inteira.
    """
    rendidos = []
    for _volta in range(max(repete, 1)):
        for passo in passos:
            if passo.hex:
                # Why: a hexadecimal literal is bytes, and a substitution inside it would be
                # half a byte on the wire, which is a command the device cannot answer.
                # Por que: um literal hexadecimal é byte, e uma substituição dentro dele seria
                # meio byte no fio, que é um comando que o aparelho não sabe responder.
                rendidos.append(passo)
                continue
            rendidos.append(
                replace(
                    passo,
                    envia=_substituir(passo.envia, substituicoes),
                    corpo=_substituir(passo.corpo, substituicoes),
                )
            )
    return tuple(rendidos)


def _substituir(texto: str, substituicoes: dict[str, str]) -> str:
    """One pass over the text: what is substituted is never scanned again.

    Uma passada sobre o texto: o que foi substituído nunca é varrido de novo.
    """

    def trocar(casamento: re.Match[str]) -> str:
        nome = casamento.group(1)
        if nome not in substituicoes:
            # Why: a command asking for a value nobody chose would put the marker itself on the
            # wire, and the device would answer an error the integrator cannot explain.
            # Por que: um comando pedindo um valor que ninguém escolheu poria o próprio
            # marcador no fio, e o aparelho responderia um erro que o integrador não explica.
            raise _Recusa(INVALID_VALUE)
        return substituicoes[nome]

    return _MARCADORES.sub(trocar, texto)


def _otimismo(acao: str, valor: object) -> dict[str, object]:
    """What the panel may show before the next poll confirms it from the device itself.

    O que o painel pode mostrar antes de o poll seguinte confirmar pelo próprio aparelho.
    """
    if acao in (ACAO_LIGAR, ACAO_DESLIGAR):
        return {"ligado": acao == ACAO_LIGAR}
    if acao == ACAO_VOLUME and type(valor) is int:
        return {"volume": valor}
    if acao == ACAO_MUDO and isinstance(valor, bool):
        return {"mudo": valor}
    if acao == ACAO_FONTE and isinstance(valor, str):
        return {"fonte": _limpo(valor)}
    if acao == ACAO_TEMPERATURA and type(valor) is int:
        return {"temperatura": valor}
    if acao in (ACAO_MODO, ACAO_VENTO) and isinstance(valor, str):
        return {acao: _limpo(valor)}
    return {}


def _chave(valor: object) -> str | None:
    """The chosen value as the key of the valores map, which is written in text.

    O valor escolhido como chave do mapa de valores, que é escrito em texto.
    """
    if isinstance(valor, bool):
        return VERDADE if valor else FALSIDADE
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    if isinstance(valor, str):
        return valor
    return None


def _limpo(texto: str) -> str:
    # Why: a source label copied from a manual with a carriage return in it became TWO commands
    # on the wire, so nothing with a control byte in it ever reaches a device.
    # Por que: um rótulo de fonte copiado do manual com um retorno de carro virava DOIS comandos
    # no fio, então nada com byte de controle jamais chega a um aparelho.
    return _CONTROLE.sub("", texto)[:VALOR_MAXIMO]


def _verdade(bruto: object, verdadeiro: str) -> bool:
    """A boolean is the word the file declared true, and everything else is false.

    Um booleano é a palavra que o arquivo declarou verdadeira, e todo o resto é falso.
    """
    return _palavra(bruto) == verdadeiro.strip().casefold()


def _palavra(bruto: object) -> str:
    if isinstance(bruto, bool):
        return VERDADE if bruto else FALSIDADE
    return str(bruto).strip().casefold()


def _inteiro_lido(bruto: object) -> int | None:
    """A whole number the device answered, or None when it answered something else.

    Um número inteiro que o aparelho respondeu, ou None quando ele respondeu outra coisa.
    """
    try:
        numero = float(str(bruto).strip())
    except (TypeError, ValueError):
        return None
    if not numero.is_integer():
        return None
    return int(numero)


def _volume_lido(bruto: object, escala: Escala | None) -> int | None:
    """The volume of the device in the contract, or None when it did not answer one.

    O volume do aparelho no contrato, ou None quando ele não respondeu um.
    """
    try:
        numero = float(str(bruto).strip())
    except (TypeError, ValueError):
        # Why: a device that answers a word where a number belongs is not a volume of zero,
        # and writing zero here would tell the panel a speaker is silent while it plays.
        # Por que: um aparelho que responde palavra onde cabe número não é volume zero, e
        # gravar zero aqui diria ao painel que uma caixa está calada enquanto ela toca.
        return None
    return escalar_para_contrato(numero, escala)


def _documento(resposta: str) -> object:
    """The answer as JSON, or nothing at all: a device answering a page is not a document.

    A resposta como JSON, ou nada: um aparelho respondendo uma página não é um documento.
    """
    try:
        return json.loads(resposta)
    except (ValueError, RecursionError):
        return None


def _no_caminho(documento: object, caminho: str) -> object:
    """The value at a dotted path, walking objects and lists, or None where it is not there.

    O valor num caminho pontilhado, andando por objetos e listas, ou None onde ele não está.
    """
    atual = documento
    for pedaco in caminho.split("."):
        if isinstance(atual, dict):
            atual = atual.get(pedaco)
        elif isinstance(atual, list) and pedaco.isdigit() and int(pedaco) < len(atual):
            atual = atual[int(pedaco)]
        else:
            return None
    # Why: an object or a list is not a value a field of Estado can hold, and str() of one
    # would put a piece of JSON on the panel card.
    # Por que: um objeto ou uma lista não é valor que um campo do Estado guarde, e o str() de
    # um poria um pedaço de JSON no cartão do painel.
    return atual if isinstance(atual, str | int | float | bool) else None
