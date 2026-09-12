# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The wire format of protocol buffers as this image writes and reads it."""

import pytest

from iphub.drivers import protobuf


@pytest.mark.parametrize(
    ("numero", "bruto"),
    [(0, b"\x00"), (1, b"\x01"), (127, b"\x7f"), (128, b"\x80\x01"), (300, b"\xac\x02")],
)
def test_um_varint_vai_e_volta(numero, bruto):
    assert protobuf.varint(numero) == bruto
    assert protobuf.ler_varint(bruto, 0) == (numero, len(bruto))


def test_uma_mensagem_e_lida_pelos_numeros_dos_campos():
    corpo = (
        protobuf.campo_inteiro(1, 2)
        + protobuf.campo_inteiro(2, 200)
        + protobuf.campo_mensagem(
            10, protobuf.campo_texto(1, "atvremote") + protobuf.campo_texto(2, "QA IP Hub")
        )
    )
    campos = protobuf.ler(corpo)
    assert protobuf.inteiro(campos, 1) == 2
    assert protobuf.inteiro(campos, 2) == 200
    assert protobuf.tem(campos, 10) and not protobuf.tem(campos, 11)
    pedido = protobuf.mensagem(campos, 10)
    assert protobuf.texto(pedido, 1) == "atvremote"
    assert protobuf.texto(pedido, 2) == "QA IP Hub"
    assert protobuf.inteiro(campos, 99, padrao=7) == 7
    assert protobuf.mensagem(campos, 99) == {}


def test_um_campo_repetido_guarda_todos_e_o_ultimo_e_o_que_vale():
    corpo = protobuf.campo_inteiro(1, 1) + protobuf.campo_inteiro(1, 5)
    campos = protobuf.ler(corpo)
    assert campos[1] == [1, 5]
    assert protobuf.inteiro(campos, 1) == 5


def test_um_booleano_e_um_inteiro_de_um_bit():
    campos = protobuf.ler(protobuf.campo_bool(1, True) + protobuf.campo_bool(2, False))
    assert protobuf.inteiro(campos, 1) == 1
    assert protobuf.inteiro(campos, 2) == 0


def test_os_quadros_saem_do_buffer_conforme_completam():
    """A read delivers a piece, several messages, or a split inside the length itself."""
    fluxo = protobuf.quadro(b"um") + protobuf.quadro(b"dois") + protobuf.quadro(b"tres")
    buffer = bytearray(fluxo[:5])
    assert list(protobuf.quadros(buffer)) == [b"um"]
    buffer += fluxo[5:9]
    assert list(protobuf.quadros(buffer)) == [b"dois"]
    buffer += fluxo[9:]
    assert list(protobuf.quadros(buffer)) == [b"tres"]
    assert not buffer


def test_um_quadro_grande_demais_nao_e_mensagem_deste_protocolo():
    buffer = bytearray(protobuf.varint(protobuf.QUADRO_MAXIMO + 1))
    with pytest.raises(protobuf.Ilegivel):
        list(protobuf.quadros(buffer))


@pytest.mark.parametrize(
    "bruto",
    [
        b"\x08",  # a varint that ran past the end
        b"\x12\x05ab",  # a length that ran past the end
        b"\x0d\x01",  # a fixed width field that ran past the end
        b"\x00\x01",  # field zero, which does not exist
        b"\x0b\x01",  # a wire type this image does not speak
        b"\xff" * 11,  # a varint longer than any integer
    ],
)
def test_uma_mensagem_torta_e_recusada_e_nao_lida_pela_metade(bruto):
    with pytest.raises(protobuf.Ilegivel):
        protobuf.ler(bruto)


def test_um_texto_que_nao_e_utf8_nao_derruba_a_leitura():
    campos = protobuf.ler(protobuf.campo_bytes(1, b"\xff\xfe"))
    assert protobuf.texto(campos, 1) == "��"
