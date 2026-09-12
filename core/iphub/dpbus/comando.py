# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The command channel of the product of audio and video, DP 143.

The panel writes one string, n:acao[:valor], and the hub turns it into one capability of
 on the equipment of number n. What arrives is data from the platform, so the
grammar is closed: a number, a word of this list, and for the words that take one, a value
in the shape that word takes. Anything else is refused as a value the data point does not
take, and nothing is ever echoed: the state comes back by the reports.
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
COM_INDICE = {ACAO_ENTRADA: "entradas", ACAO_ATALHO: "atalhos", ACAO_MODO: "modos"}
ACOES = (*SEM_VALOR, *COM_INDICE, ACAO_TECLA, ACAO_EXTRA)

# What each word of the channel is in the vocabulary.
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

# An extra command is written on the wire of the device by the driver, so it stays
# short and printable; a list index never needs more than two digits.
VALOR_MAXIMO = 64
_QUADRO = re.compile(r"([1-9][0-9]?):([a-z_]{1,16})(?::(.{1,64}))?")
_INDICE = re.compile(r"[1-9][0-9]?")


@dataclass(frozen=True)
class Comando:
    """One command of the channel already read: the number, the word and its value."""

    numero: int
    acao: str
    valor: str | None = None

    @property
    def capacidade(self) -> str:
        return CAPACIDADE_DA_ACAO[self.acao]

    @property
    def indice(self) -> int:
        """The 1-based index of a word that takes one, which the reader already checked."""
        return int(self.valor or 0)


def ler(texto: object, capacidade: int) -> Comando | None:
    """The command of one DP 143 string, or None for a string outside the grammar."""
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
