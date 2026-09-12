# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Emotiva driver against a simulated processor speaking XML over UDP."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import emotiva
from iphub.drivers.nativos.emotiva import Emotiva

IP = "127.0.0.1"

ESTADO = {
    "power": "On",
    "volume": "-42.5",
    "source": "HDMI 1",
    "mute": "Off",
    "input_1": "HDMI 1",
    "input_2": "HDMI 2",
    "input_3": "Blu-ray",
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "processador-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Processador(asyncio.DatagramProtocol):
    """A processor that answers an update with the values it has, and files the commands."""

    def __init__(self, estado: dict[str, str], *, calado: bool = False) -> None:
        self.estado = dict(estado)
        self.calado = calado
        self.comandos: list[str] = []
        self.pedidos: list[str] = []
        self.transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transporte) -> None:
        self.transporte = transporte

    def datagram_received(self, dados: bytes, remetente) -> None:
        texto = dados.decode("utf-8", errors="replace")
        if "emotivaControl" in texto:
            self.comandos.append(texto)
            return
        self.pedidos.append(texto)
        if self.calado or self.transporte is None:
            return
        pedidos = [parte.split(" ")[0] for parte in texto.split("<")[3:] if parte]
        dentro = "".join(
            f'<{nome} value="{self.estado[nome]}" visible="true" />'
            for nome in pedidos
            if nome in self.estado
        )
        resposta = f"{emotiva.CABECALHO}<emotivaUpdate>{dentro}</emotivaUpdate>"
        self.transporte.sendto(resposta.encode(), remetente)


@pytest.fixture
async def processador(monkeypatch):
    """A processor already listening, with the driver bound to a port of the test."""
    abertos: list[asyncio.DatagramTransport] = []
    drivers: list[Emotiva] = []

    async def abrir(estado=None, **campos):
        laco = asyncio.get_running_loop()
        protocolo = _Processador(
            ESTADO if estado is None else estado, calado=campos.pop("calado", False)
        )
        transporte, _ = await laco.create_datagram_endpoint(lambda: protocolo, local_addr=(IP, 0))
        abertos.append(transporte)
        porta = transporte.get_extra_info("sockname")[1]
        # The hub binds a port of its own, and the processor answers where the request came
        # from, so on one machine the two have to be different numbers.
        monkeypatch.setattr(emotiva, "PORTA_LOCAL", 0)
        monkeypatch.setattr(emotiva, "RESPOSTA_S", 1.0)
        emotiva._Canal._abertos.clear()
        driver = Emotiva(_Cadastro(campos={"porta": str(porta), **campos}))
        drivers.append(driver)
        return protocolo, driver

    yield abrir
    for driver in drivers:
        await driver.parar()
    for transporte in abertos:
        transporte.close()
    emotiva._Canal._abertos.clear()


def test_o_manifesto_e_valido():
    validar(Emotiva.MANIFESTO)
    assert Emotiva.MANIFESTO.categoria == "receiver"


async def test_um_poll_le_o_estado_e_os_nomes_das_entradas(processador):
    aparelho, driver = await processador()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # -42.5 dB of a scale that goes from -96 to 11 is 50 on the contract.
    assert estado.volume == 50
    assert estado.mudo is False
    assert estado.fonte == "HDMI 1"
    assert set(estado.fontes) == {"HDMI 1", "HDMI 2", "Blu-ray"}
    await driver.parar()


async def test_o_processador_escreve_mute_no_proprio_volume(processador):
    """When it is muted there is no level to read, which is what the word in the volume says."""
    aparelho, driver = await processador({**ESTADO, "volume": "Mute"})
    await driver.atualizar()
    assert driver.estado().mudo is True
    assert driver.estado().volume is None
    await driver.parar()


async def test_a_faixa_de_decibeis_do_cadastro_muda_a_conversao(processador):
    aparelho, driver = await processador(db_minimo="-80", db_maximo="0")
    await driver.atualizar()
    # -42.5 dB of a scale that goes from -80 to 0 is 47 on the contract.
    assert driver.estado().volume == 47
    await driver.parar()


async def test_ligar_desligar_volume_e_mudo_mandam_o_comando_do_protocolo(processador):
    aparelho, driver = await processador()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("mudo", True) is None
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: len(aparelho.comandos) == 4)
    assert "power_on" in aparelho.comandos[0]
    assert "power_off" in aparelho.comandos[1]
    assert "mute_on" in aparelho.comandos[2]
    assert 'set_volume value="-42.5"' in aparelho.comandos[3]
    assert await driver.executar("mudo", "sim") == "invalid_value"
    assert await driver.executar("volume", 101) == "invalid_value"
    await driver.parar()


async def test_a_entrada_viaja_pelo_nome_do_dono_ou_pelo_numero(processador):
    aparelho, driver = await processador()
    await driver.atualizar()
    assert await driver.executar("fonte", "Blu-ray") is None
    await _ate(lambda: aparelho.comandos)
    assert "source_3" in aparelho.comandos[-1]
    assert driver.estado().fonte == "Blu-ray"
    assert await driver.executar("fonte", "2") is None
    await _ate(lambda: "source_2" in aparelho.comandos[-1])
    assert await driver.executar("fonte", "nao existe") == "invalid_value"
    assert await driver.executar("fonte", 3) == "invalid_value"
    await driver.parar()


async def test_um_processador_que_nao_responde_e_offline_depois_de_dois_polls(processador):
    aparelho, driver = await processador(calado=True)
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == ""
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_dois_cadastros_dividem_o_mesmo_socket(processador):
    """Two sockets on one port is what the operating system refuses, so the registrations of
    an installation share one and each waits for the datagram of its own address."""
    aparelho, primeiro = await processador()
    await primeiro.atualizar()
    segundo = Emotiva(_Cadastro(identidade="outro", campos=dict(primeiro.cadastro.campos)))
    try:
        await segundo.atualizar()
        assert segundo.estado().online is True
        assert len(emotiva._Canal._abertos) == 1
    finally:
        await segundo.parar()
    await primeiro.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the processor to read what was written to it, on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)
