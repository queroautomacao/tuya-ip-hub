# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6: a driver reads what the device answered, not what fit in one TCP segment.

Seção 6: um driver lê o que o aparelho respondeu, não o que coube num segmento TCP.
"""

import pytest

from iphub.drivers import corpo


class _Fluxo:
    """A body that arrives in pieces, which is what a stream of the aiohttp does.

    Um corpo que chega em pedaços, que é o que um fluxo do aiohttp faz.
    """

    def __init__(self, *pedacos: bytes) -> None:
        self.pedacos = list(pedacos)
        self.pedidos: list[int] = []

    async def read(self, n: int = -1) -> bytes:
        self.pedidos.append(n)
        if not self.pedacos:
            return b""
        pedaco = self.pedacos.pop(0)
        return pedaco[:n]


async def test_le_o_corpo_inteiro_e_nao_so_o_primeiro_segmento():
    # Why: one read returns only what the buffer already holds, so a status object that
    # arrived in two segments stopped being json and the device read as broken.
    # Por que: uma leitura só devolve o que o buffer já tem, então um objeto de estado que
    # chegou em dois segmentos deixava de ser json e o aparelho lia como quebrado.
    fluxo = _Fluxo(b'{"uuid": "AB', b'CD", "vol"', b': "50"}')
    assert await corpo.inteiro(fluxo, 65536) == b'{"uuid": "ABCD", "vol": "50"}'


async def test_para_no_teto_e_nunca_pede_mais_do_que_falta():
    # Why: the answer of a device on the customer LAN never sizes the memory of the hub.
    # Por que: a resposta de um aparelho na LAN do cliente nunca dimensiona a memória do hub.
    fluxo = _Fluxo(b"a" * 40, b"b" * 40, b"c" * 40)
    assert await corpo.inteiro(fluxo, 50) == b"a" * 40 + b"b" * 10
    assert fluxo.pedidos == [50, 10]


async def test_fim_do_fluxo_encerra_a_leitura():
    fluxo = _Fluxo(b"abc")
    assert await corpo.inteiro(fluxo, 100) == b"abc"


@pytest.mark.parametrize("maximo", [0, -1])
async def test_teto_sem_espaco_nao_le_nada(maximo):
    fluxo = _Fluxo(b"abc")
    assert await corpo.inteiro(fluxo, maximo) == b""
    assert fluxo.pedidos == []
