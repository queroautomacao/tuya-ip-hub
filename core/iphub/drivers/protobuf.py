# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""The wire format of protocol buffers, only the part a driver of this image needs.

A television that speaks this format sends a handful of message shapes and expects a handful
back, all of them a few integers and a few strings, so what is written here is the encoding
itself and not a schema compiler: a field is a number plus a value, a message is the
concatenation of its fields, and a frame is a message with its length in front. Reading is
the same rule backwards, into a dict of field number to values, which the caller reads by the
numbers of the protocol it speaks. Nothing is generated at build time and nothing is loaded at
runtime, which is what keeps a message from a device to being bytes and never code.
"""

from collections.abc import Iterator

# Wire types of the format. Only these four exist in the messages of this image, and a field
# of any other type is what makes a message unreadable instead of half read.
VARINT = 0
BITS64 = 1
TAMANHO = 2
BITS32 = 5

# A frame longer than this is a device that lost the stream or is not the device it claims,
# and reading it would be spending memory on garbage.
QUADRO_MAXIMO = 256 * 1024
VARINT_MAXIMO = 10


class Ilegivel(ValueError):
    """A message that does not parse: the stream cannot be trusted from here on."""


def varint(numero: int) -> bytes:
    """One unsigned integer in the seven bits per byte form the format uses."""
    if numero < 0:
        raise Ilegivel("a varint is never negative here")
    saida = bytearray()
    while True:
        byte = numero & 0x7F
        numero >>= 7
        saida.append(byte | (0x80 if numero else 0))
        if not numero:
            return bytes(saida)


def ler_varint(dados: bytes, posicao: int) -> tuple[int, int]:
    """The integer at that position and where it ends."""
    valor = 0
    deslocamento = 0
    for passo in range(VARINT_MAXIMO):
        if posicao + passo >= len(dados):
            raise Ilegivel("a varint ran past the end of the message")
        byte = dados[posicao + passo]
        valor |= (byte & 0x7F) << deslocamento
        if not byte & 0x80:
            return valor, posicao + passo + 1
        deslocamento += 7
    raise Ilegivel("a varint longer than any integer of this format")


def campo_inteiro(numero: int, valor: int) -> bytes:
    return varint(numero << 3 | VARINT) + varint(valor)


def campo_bytes(numero: int, valor: bytes) -> bytes:
    return varint(numero << 3 | TAMANHO) + varint(len(valor)) + valor


def campo_texto(numero: int, valor: str) -> bytes:
    return campo_bytes(numero, valor.encode("utf-8"))


def campo_mensagem(numero: int, corpo: bytes) -> bytes:
    return campo_bytes(numero, corpo)


def campo_bool(numero: int, valor: bool) -> bytes:
    return campo_inteiro(numero, 1 if valor else 0)


def ler(dados: bytes) -> dict[int, list[int | bytes]]:
    """A message as field number to values, repeated fields keeping every one of them."""
    campos: dict[int, list[int | bytes]] = {}
    posicao = 0
    while posicao < len(dados):
        etiqueta, posicao = ler_varint(dados, posicao)
        numero, tipo = etiqueta >> 3, etiqueta & 0x7
        if numero == 0:
            raise Ilegivel("field zero does not exist in this format")
        if tipo == VARINT:
            valor, posicao = ler_varint(dados, posicao)
        elif tipo == TAMANHO:
            tamanho, posicao = ler_varint(dados, posicao)
            fim = posicao + tamanho
            if tamanho < 0 or fim > len(dados):
                raise Ilegivel("a length delimited field ran past the end of the message")
            valor = dados[posicao:fim]
            posicao = fim
        elif tipo in (BITS64, BITS32):
            largura = 8 if tipo == BITS64 else 4
            if posicao + largura > len(dados):
                raise Ilegivel("a fixed width field ran past the end of the message")
            valor = int.from_bytes(dados[posicao : posicao + largura], "little")
            posicao += largura
        else:
            raise Ilegivel(f"wire type {tipo} is not one this image speaks")
        campos.setdefault(numero, []).append(valor)
    return campos


def inteiro(campos: dict[int, list[int | bytes]], numero: int, padrao: int = 0) -> int:
    """The last value of an integer field, or the default when the message omitted it.

    The last and not the first because that is what the format says a repeated scalar means,
    and a device that sends a field twice means the second one.
    """
    valores = [valor for valor in campos.get(numero, ()) if isinstance(valor, int)]
    return valores[-1] if valores else padrao


def crua(campos: dict[int, list[int | bytes]], numero: int) -> bytes:
    valores = [valor for valor in campos.get(numero, ()) if isinstance(valor, bytes)]
    return valores[-1] if valores else b""


def texto(campos: dict[int, list[int | bytes]], numero: int) -> str:
    return crua(campos, numero).decode("utf-8", errors="replace")


def mensagem(campos: dict[int, list[int | bytes]], numero: int) -> dict[int, list[int | bytes]]:
    """A nested message as its own fields, empty when the message did not carry it."""
    bruto = crua(campos, numero)
    return ler(bruto) if bruto else {}


def tem(campos: dict[int, list[int | bytes]], numero: int) -> bool:
    return numero in campos


def quadro(corpo: bytes) -> bytes:
    """One message with its length in front, which is how it travels on the socket."""
    return varint(len(corpo)) + corpo


def quadros(buffer: bytearray) -> Iterator[bytes]:
    """Every complete message in the buffer, consumed as it is yielded.

    A read may deliver a piece of a frame, several frames, or a split in the middle of the
    length itself, so what is not complete stays in the buffer for the next read.
    """
    while buffer:
        try:
            tamanho, inicio = ler_varint(bytes(buffer), 0)
        except Ilegivel:
            if len(buffer) < VARINT_MAXIMO:
                return
            raise
        if tamanho > QUADRO_MAXIMO:
            raise Ilegivel(f"a frame of {tamanho} bytes is not a message of this protocol")
        fim = inicio + tamanho
        if len(buffer) < fim:
            return
        corpo = bytes(buffer[inicio:fim])
        del buffer[:fim]
        yield corpo
