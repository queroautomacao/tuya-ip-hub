# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8: the command channel of the product of audio and video, DP 143.

The panel writes one string, n:acao[:valor], and the hub turns it into one capability of
section 6 on the equipment of number n. What arrives is data from the platform, so the
grammar is closed: a number, a word of this list, and for the words that take one, a value
in the shape that word takes. Anything else is refused as a value the data point does not
take, and nothing is ever echoed: the state comes back by the reports.

Seção 8: o canal de comando do produto de áudio e vídeo, o DP 143.

O painel escreve uma string, n:acao[:valor], e o hub a transforma numa capacidade da seção
6 no equipamento do número n. O que chega é dado da plataforma, então a gramática é fechada:
um número, uma palavra desta lista, e para as palavras que a recebem, um valor na forma que
aquela palavra aceita. O resto é recusado como valor que o data point não aceita, e nada é
ecoado: o estado volta pelos reports.
"""

import re
from dataclasses import dataclass

from iphub.drivers.manifesto import TECLAS

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_MUDO = "mudo"
ACAO_ENTRADA = "entrada"
ACAO_ATALHO = "atalho"
ACAO_MODO = "modo"
ACAO_TECLA = "tecla"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"
ACAO_EXTRA = "extra"

SEM_VALOR = (
    ACAO_LIGAR,
    ACAO_DESLIGAR,
    ACAO_MUDO,
    ACAO_TOCAR,
    ACAO_PAUSAR,
    ACAO_PARAR,
    ACAO_PROXIMA,
    ACAO_ANTERIOR,
)
# The words that take an index into a list of the registration, and which list.
# As palavras que recebem um índice numa lista do cadastro, e qual lista.
COM_INDICE = {ACAO_ENTRADA: "entradas", ACAO_ATALHO: "atalhos", ACAO_MODO: "modos"}
ACOES = (*SEM_VALOR, *COM_INDICE, ACAO_TECLA, ACAO_EXTRA)

# What each word of the channel is in the vocabulary of section 6.
# O que cada palavra do canal é no vocabulário da seção 6.
CAPACIDADE_DA_ACAO = {
    ACAO_LIGAR: "ligar",
    ACAO_DESLIGAR: "desligar",
    ACAO_MUDO: "mudo",
    ACAO_ENTRADA: "fonte",
    ACAO_ATALHO: "atalho",
    ACAO_MODO: "modo",
    ACAO_TECLA: "tecla",
    ACAO_TOCAR: "tocar",
    ACAO_PAUSAR: "pausar",
    ACAO_PARAR: "parar",
    ACAO_PROXIMA: "proxima",
    ACAO_ANTERIOR: "anterior",
    ACAO_EXTRA: "comando_extra",
}

# Why: an extra command is written on the wire of the device by the driver, so it stays
# short and printable; a list index never needs more than two digits.
# Por que: um comando extra é escrito no fio do aparelho pelo driver, então fica curto e
# imprimível; um índice de lista nunca precisa de mais de dois dígitos.
VALOR_MAXIMO = 64
_QUADRO = re.compile(r"([1-9][0-9]?):([a-z_]{1,16})(?::(.{1,64}))?")
_INDICE = re.compile(r"[1-9][0-9]?")


@dataclass(frozen=True)
class Comando:
    """One command of the channel already read: the number, the word and its value.

    Um comando do canal já lido: o número, a palavra e o valor dela.
    """

    numero: int
    acao: str
    valor: str | None = None

    @property
    def capacidade(self) -> str:
        return CAPACIDADE_DA_ACAO[self.acao]

    @property
    def indice(self) -> int:
        """The 1-based index of a word that takes one, which the reader already checked.

        O índice a partir de 1 de uma palavra que o recebe, que o leitor já conferiu.
        """
        return int(self.valor or 0)


def ler(texto: object, capacidade: int) -> Comando | None:
    """The command of one DP 143 string, or None for a string outside the grammar.

    O comando de uma string do DP 143, ou None para uma string fora da gramática.
    """
    if not isinstance(texto, str):
        return None
    casamento = _QUADRO.fullmatch(texto)
    if casamento is None:
        return None
    numero = int(casamento.group(1))
    acao = casamento.group(2)
    valor = casamento.group(3)
    if not 1 <= numero <= capacidade or acao not in ACOES:
        return None
    if acao in SEM_VALOR:
        return None if valor is not None else Comando(numero, acao)
    if valor is None or not valor.isprintable():
        return None
    if acao in COM_INDICE:
        return Comando(numero, acao, valor) if _INDICE.fullmatch(valor) else None
    if acao == ACAO_TECLA:
        return Comando(numero, acao, valor) if valor in TECLAS else None
    return Comando(numero, acao, valor)
