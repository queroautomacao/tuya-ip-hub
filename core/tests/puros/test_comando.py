# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8 under attack: the command channel is a closed grammar, and everything outside it
is None, never a guess and never an exception.

What arrives on DP 143 is data from the platform: a number of the licence, a word of the
list, and for the words that take one, a value in the shape that word takes. A word without
a value refuses one, an index is two digits at most, a key is a word of section 6 and an
extra command is short and printable because the driver writes it on the wire of the device.

Seção 8 sob ataque: o canal de comando é uma gramática fechada, e tudo fora dela é None,
nunca um palpite e nunca uma exceção.

O que chega no DP 143 é dado da plataforma: um número da licença, uma palavra da lista, e
para as palavras que a recebem, um valor na forma que aquela palavra aceita. Uma palavra sem
valor recusa um, um índice tem no máximo dois dígitos, uma tecla é uma palavra da seção 6 e um
comando extra é curto e imprimível porque o driver o escreve no fio do aparelho.
"""

import dataclasses

import pytest

from iphub.config import LISTAS
from iphub.dpbus.comando import (
    ACOES,
    CAPACIDADE_DA_ACAO,
    COM_INDICE,
    SEM_VALOR,
    VALOR_MAXIMO,
    Comando,
    ler,
)
from iphub.drivers.manifesto import CAPACIDADES, TECLAS

CAPACIDADE = 12


def test_o_vocabulario_do_canal_e_o_da_secao_8():
    assert set(ACOES) == {
        "ligar",
        "desligar",
        "mudo",
        "entrada",
        "atalho",
        "modo",
        "tecla",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "extra",
    }
    assert set(SEM_VALOR) == {
        "ligar",
        "desligar",
        "mudo",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
    }
    assert COM_INDICE == {"entrada": "entradas", "atalho": "atalhos", "modo": "modos"}
    assert set(COM_INDICE.values()) == set(LISTAS)
    # Every word of the channel is one capability of section 6, and nothing outside it.
    # Toda palavra do canal é uma capacidade da seção 6, e nada fora dela.
    assert set(CAPACIDADE_DA_ACAO) == set(ACOES)
    assert set(CAPACIDADE_DA_ACAO.values()) <= set(CAPACIDADES)
    assert CAPACIDADE_DA_ACAO["entrada"] == "fonte"
    assert CAPACIDADE_DA_ACAO["extra"] == "comando_extra"


@pytest.mark.parametrize("acao", SEM_VALOR)
def test_uma_palavra_sem_valor_le_o_numero_e_a_palavra(acao):
    lido = ler(f"3:{acao}", CAPACIDADE)
    assert lido == Comando(3, acao, None)
    assert lido.capacidade == CAPACIDADE_DA_ACAO[acao]
    assert lido.indice == 0


@pytest.mark.parametrize("acao", SEM_VALOR)
def test_uma_palavra_sem_valor_recusa_um_valor(acao):
    assert ler(f"3:{acao}:1", CAPACIDADE) is None
    assert ler(f"3:{acao}:on", CAPACIDADE) is None
    assert ler(f"3:{acao}:", CAPACIDADE) is None


@pytest.mark.parametrize(
    ("texto", "numero"), [("1:ligar", 1), ("9:ligar", 9), ("10:ligar", 10), ("12:ligar", 12)]
)
def test_o_numero_vai_de_1_ate_a_capacidade(texto, numero):
    assert ler(texto, CAPACIDADE).numero == numero


@pytest.mark.parametrize(
    "texto", ["0:ligar", "13:ligar", "01:ligar", "100:ligar", "-1:ligar", "1.0:ligar", " 1:ligar"]
)
def test_um_numero_fora_da_licenca_e_recusado(texto):
    assert ler(texto, CAPACIDADE) is None


def test_a_capacidade_e_a_da_licenca_que_le():
    """The product of air numbers eight, so the same string is a command on one licence and
    garbage on the other.

    O produto de ar numera oito, então a mesma string é comando numa licença e lixo na outra.
    """
    assert ler("8:ligar", 8) == Comando(8, "ligar")
    assert ler("9:ligar", 8) is None
    assert ler("1:ligar", 0) is None


@pytest.mark.parametrize("acao", sorted(COM_INDICE))
def test_uma_palavra_com_indice_exige_um_indice_de_1_a_99(acao):
    assert ler(f"1:{acao}:1", CAPACIDADE) == Comando(1, acao, "1")
    assert ler(f"1:{acao}:99", CAPACIDADE).indice == 99
    assert ler(f"1:{acao}:7", CAPACIDADE).capacidade == CAPACIDADE_DA_ACAO[acao]
    for valor in ("0", "100", "01", "a", "1a", "-1", "1.0", " 1", "1 ", "1:2"):
        assert ler(f"1:{acao}:{valor}", CAPACIDADE) is None, valor
    assert ler(f"1:{acao}:", CAPACIDADE) is None
    assert ler(f"1:{acao}", CAPACIDADE) is None


def test_a_tecla_e_uma_palavra_de_teclas_e_nada_mais():
    """Section 6: the panel speaks the words of TECLAS and the driver translates each one, so
    the channel never lets a word the vocabulary does not have through.

    Seção 6: o painel fala as palavras de TECLAS e o driver traduz cada uma, então o canal
    nunca deixa passar uma palavra que o vocabulário não tem.
    """
    for tecla in TECLAS:
        lido = ler(f"2:tecla:{tecla}", CAPACIDADE)
        assert lido == Comando(2, "tecla", tecla) and lido.capacidade == "tecla"
    for valor in ("voar", "CANAL_MAIS", "canal mais", "1", "canal_mais ", " ok"):
        assert ler(f"2:tecla:{valor}", CAPACIDADE) is None, valor
    assert ler("2:tecla", CAPACIDADE) is None
    assert ler("2:tecla:", CAPACIDADE) is None


def test_o_extra_leva_qualquer_valor_imprimivel_de_ate_64_caracteres():
    """An extra command is written on the wire of the device by the driver, so it stays short
    and printable, and it may carry its own colons.

    Um comando extra é escrito no fio do aparelho pelo driver, então fica curto e imprimível,
    e pode carregar os próprios dois pontos.
    """
    assert VALOR_MAXIMO == 64
    lido = ler("1:extra:preset:3", CAPACIDADE)
    assert lido == Comando(1, "extra", "preset:3") and lido.capacidade == "comando_extra"
    assert ler("1:extra:" + "x" * VALOR_MAXIMO, CAPACIDADE).valor == "x" * VALOR_MAXIMO
    assert ler("1:extra:" + "x" * (VALOR_MAXIMO + 1), CAPACIDADE) is None
    assert ler("1:extra:a b", CAPACIDADE).valor == "a b"
    assert ler("1:extra:Área", CAPACIDADE).valor == "Área"
    for valor in ("\x01", "a\tb", "a\nb", "a\rb", " ", ""):
        assert ler(f"1:extra:{valor}", CAPACIDADE) is None, repr(valor)
    assert ler("1:extra", CAPACIDADE) is None


@pytest.mark.parametrize(
    "texto",
    [
        None,
        5,
        1.0,
        True,
        b"1:ligar",
        ["1:ligar"],
        {"t": "1:ligar"},
        "",
        ":",
        "1",
        "1:",
        ":ligar",
        "1:LIGAR",
        "1:Ligar",
        "1:voar",
        "1:volume:30",
        "1:grupo:1",
        "1:cena:1",
        "1:ligar\n",
        "1;ligar",
        "1:ligar ",
        "1::ligar",
        "1:" + "a" * 17,
        "1:entrada:1:2",
        "1:ligar:desligar",
    ],
)
def test_lixo_e_none_e_nunca_excecao(texto):
    """The level, the group and the scene have data points of their own, and a word that is
    not in the list is garbage like any other.

    O nível, o grupo e a cena têm data points próprios, e uma palavra que não está na lista é
    lixo como qualquer outro.
    """
    assert ler(texto, CAPACIDADE) is None


def test_o_comando_lido_e_congelado():
    lido = ler("1:ligar", CAPACIDADE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        lido.numero = 2
    assert Comando(1, "ligar") == Comando(1, "ligar", None)
