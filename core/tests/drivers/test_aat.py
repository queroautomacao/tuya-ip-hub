# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Under attack: one registration of this driver is ONE ZONE of the amplifier."""

from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import aat
from iphub.drivers.nativos.aat import Aat
from iphub.drivers.simulado import ServidorLinha


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "aat-sala"
    tipo: str = "multiroom_aat"
    nome: str = "Sala"
    ip: str = "192.0.2.10"
    campos: dict[str, str] = field(default_factory=lambda: {"zona": "1"})
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


# The answer the manual prints for a PMR-7: five fields of the product and then seven of each
# of the six zones, in a fixed order that has no names on the wire.
def _getall(*, power: str = "ON", volume1: str = "30", mudo1: str = "OFF") -> bytes:
    zonas = []
    for numero in range(1, 7):
        entrada = "6" if numero == 1 else "5"
        volume = volume1 if numero == 1 else "30"
        mudo = mudo1 if numero == 1 else "OFF"
        zonas += [entrada, volume, mudo, "14", "14", "20", "7"]
    corpo = " ".join(["PMR7", "V1.13", power, "12345", "60", *zonas])
    return f"[r001 GETALL {corpo}]\r".encode()


def _respostas(**extras: bytes) -> dict[bytes, bytes]:
    respostas = {b"[t001 GETALL]": _getall()}
    respostas.update(extras)
    return respostas


def _driver(aparelho: ServidorLinha, *, zona: str = "1") -> Aat:
    anfitriao, porta = aparelho.endereco
    return Aat(_Cadastro(ip=anfitriao, campos={"zona": zona, "porta": str(porta)}))


def test_o_manifesto_e_valido_e_nao_promete_o_que_o_fio_nao_tem():
    """What the protocol has no command for is omitted, never refused later.

    The amplifier has no transport, no shortcut and no grouping in the shape
    defines, so none of the three is declared; a button that only ever answers nao_suportado
    is a button the customer learns to distrust.
    """
    manifesto = Aat.MANIFESTO
    assert validar(manifesto) is None
    assert "agrupar" not in manifesto.capacidades
    assert "tocar" not in manifesto.capacidades
    assert "atalho" not in manifesto.capacidades
    assert manifesto.teclas == ("mais", "menos")


async def test_um_poll_le_a_zona_deste_cadastro_dentro_do_getall():
    """One GETALL carries every zone, and this registration reads only its own."""
    async with ServidorLinha(_respostas()) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        estado = driver.estado()
        await driver.parar()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.fonte == "6"
    assert estado.mudo is False
    # 30 of 87 is 34 of 100, and the panel never sees a number of the amplifier.
    assert estado.volume == 34


async def test_a_zona_dois_le_os_campos_dela_e_nao_os_da_um():
    """The zone of the registration is what decides which seven fields are read."""
    async with ServidorLinha(_respostas()) as aparelho:
        driver = _driver(aparelho, zona="2")
        await driver.atualizar()
        estado = driver.estado()
        await driver.parar()
    assert estado.fonte == "5"


async def test_uma_zona_que_o_produto_nao_tem_e_recusada_e_nao_inventada():
    """A zone the product does not have is a registration nothing can reach."""
    curto = b"[r001 GETALL PMR8 V1.13 ON 12345 60 1 30 OFF 14 14 20 7]\r"
    async with ServidorLinha({b"[t001 GETALL]": curto}) as aparelho:
        driver = _driver(aparelho, zona="4")
        await driver.atualizar()
        estado = driver.estado()
        await driver.parar()
    assert estado.online is False
    assert estado.detalhe == "invalid_value"


@pytest.mark.parametrize(
    ("acao", "valor", "linha"),
    [
        ("ligar", None, b"[t001 ZSTDBYOFF 1]"),
        ("desligar", None, b"[t001 ZSTDBYON 1]"),
        ("mudo", True, b"[t001 MUTEON 1]"),
        ("mudo", False, b"[t001 MUTEOFF 1]"),
        ("fonte", "3", b"[t001 INPSET 1 3]"),
        ("tecla", "mais", b"[t001 VOL+ 1]"),
        ("tecla", "menos", b"[t001 VOL- 1]"),
    ],
)
async def test_cada_acao_vai_no_comando_do_manual_com_a_zona_do_cadastro(acao, valor, linha):
    eco = linha.replace(b"[t001 ", b"[r001 ") + b"\r"
    async with ServidorLinha({linha: eco}) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar(acao, valor) is None
        await driver.parar()
    assert linha in aparelho.recebidas


async def test_o_volume_da_secao_6_vira_o_volume_do_aparelho_e_volta():
    """The 0 to 100 of the bus and the 0 to 87 of the amplifier meet in one place."""
    assert aat._para_o_aparelho(0) == 0
    assert aat._para_o_aparelho(100) == 87
    assert aat._para_o_aparelho(50) == 44
    assert aat._do_aparelho(87) == 100
    assert aat._do_aparelho(0) == 0
    linha = b"[t001 VOLSET 1 44]"
    async with ServidorLinha({linha: b"[r001 VOLSET 1 44]\r"}) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("volume", 50) is None
        assert driver.estado().volume == 50
        await driver.parar()
    assert linha in aparelho.recebidas


@pytest.mark.parametrize("valor", [-1, 101, "50", 50.0, True, None])
async def test_um_volume_fora_da_escala_nunca_chega_ao_fio(valor):
    async with ServidorLinha(_respostas()) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("volume", valor) == "invalid_value"
        await driver.parar()
    assert aparelho.recebidas == []


@pytest.mark.parametrize("valor", ["0", "9", "nove", "", None, 0, 9])
async def test_uma_entrada_fora_de_um_a_oito_nunca_chega_ao_fio(valor):
    async with ServidorLinha(_respostas()) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("fonte", valor) == "invalid_value"
        await driver.parar()
    assert aparelho.recebidas == []


async def test_uma_tecla_que_o_manifesto_nao_declara_nao_vira_comando():
    async with ServidorLinha(_respostas()) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("tecla", "canal_mais") == "nao_suportado"
        await driver.parar()
    assert aparelho.recebidas == []


@pytest.mark.parametrize("valor", ["[t001 PWRON]", "MODEL]", "a\rb", "", 7, None])
async def test_um_comando_extra_que_monta_quadro_na_mao_e_recusado(valor):
    """The frame and the sequence are of the protocol and never of the operator."""
    async with ServidorLinha(_respostas()) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("comando_extra", valor) == "invalid_value"
        await driver.parar()
    assert aparelho.recebidas == []


async def test_um_comando_extra_do_manual_viaja_dentro_do_quadro():
    linha = b"[t001 BASSSET 1 14]"
    async with ServidorLinha({linha: b"[r001 BASSSET 1 14]\r"}) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("comando_extra", "bassset 1 14") is None
        await driver.parar()
    assert linha in aparelho.recebidas


async def test_uma_linha_nao_solicitada_nao_e_lida_como_resposta():
    """Of this file: the amplifier pushes a line when its front panel moves.

    Reading it as the answer of the question in flight would publish the state of
    another zone under the name of this registration.
    """
    empurrada = b"[n007 VOLSET 4 60]\r"
    async with ServidorLinha({b"[t001 GETALL]": empurrada + _getall()}) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        estado = driver.estado()
        await driver.parar()
    assert estado.online is True
    assert estado.volume == 34


async def test_uma_resposta_de_outro_comando_e_recusa_e_nao_estado():
    """The manual defines no frame for a refusal, so anything that is not the answer is one."""
    async with ServidorLinha({b"[t001 GETALL]": b"[r001 PWRGET ON]\r"}) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        await driver.atualizar()
        estado = driver.estado()
        await driver.parar()
    assert estado.online is False
    assert estado.detalhe == "erro_aparelho"


async def test_um_aparelho_que_nao_responde_fica_offline_e_diz_o_codigo():
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        await driver.atualizar()
        estado = driver.estado()
        await driver.parar()
    assert estado.online is False
    assert estado.detalhe == "eq_offline"


async def test_o_identificar_nao_inventa_identidade_porque_o_fio_nao_tem_uma():
    """The amplifier says only its MODEL, and two of one model answer the same."""
    assert await Aat.identificar("192.0.2.10") is None
