# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Samsung legacy driver against a simulated television of the generation before Tizen."""

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import samsung_legado
from iphub.drivers.nativos.samsung_legado import SamsungLegado

IP = "127.0.0.1"
NOME_DA_TV = b"UE46ES6900"


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Tv:
    """A television that answers the request for permission and files every key it got."""

    def __init__(self, *, veredito: bytes = samsung_legado.CONCEDIDO, avisar: bool = False) -> None:
        self.veredito = veredito
        # An avisar television sends the packet of the popup first, like a set of that year.
        self.avisar = avisar
        self.pareamentos: list[list[str]] = []
        self.teclas: list[str] = []
        self.endereco: tuple[str, int] = ("", 0)
        self._servidor: asyncio.Server | None = None
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self) -> tuple[str, int]:
        self._servidor = await asyncio.start_server(self._atender, IP, 0)
        self.endereco = self._servidor.sockets[0].getsockname()[:2]
        return self.endereco

    async def parar(self) -> None:
        servidor, self._servidor = self._servidor, None
        for tarefa in self._tarefas:
            tarefa.cancel()
        if servidor is None:
            return
        servidor.close()
        with suppress(TimeoutError):
            async with asyncio.timeout(2.0):
                await servidor.wait_closed()

    async def _atender(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        tarefa = asyncio.current_task()
        if tarefa is not None:
            self._tarefas.add(tarefa)
        try:
            while True:
                await leitor.readexactly(1)
                await _bloco(leitor)
                carga = await _bloco(leitor)
                await self._responder(carga, escritor)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if tarefa is not None:
                self._tarefas.discard(tarefa)
            escritor.close()

    async def _responder(self, carga: bytes, escritor: asyncio.StreamWriter) -> None:
        if carga.startswith(b"\x64\x00"):
            self.pareamentos.append(_campos(carga[2:]))
            if self.avisar:
                _enviar(escritor, samsung_legado.ESPERANDO)
            _enviar(escritor, self.veredito)
            await escritor.drain()
            return
        self.teclas.extend(_campos(carga[3:]))


def _enviar(escritor: asyncio.StreamWriter, veredito: bytes) -> None:
    escritor.write(
        b"\x00"
        + len(NOME_DA_TV).to_bytes(2, "little")
        + NOME_DA_TV
        + len(veredito).to_bytes(2, "little")
        + veredito
    )


async def _bloco(leitor: asyncio.StreamReader) -> bytes:
    tamanho = int.from_bytes(await leitor.readexactly(2), "little")
    return await leitor.readexactly(tamanho)


def _campos(bruto: bytes) -> list[str]:
    """The base64 blocks of a payload, back as the text the driver put in them."""
    achados: list[str] = []
    while len(bruto) >= 2:
        tamanho = int.from_bytes(bruto[:2], "little")
        achados.append(base64.b64decode(bruto[2 : 2 + tamanho]).decode("utf-8"))
        bruto = bruto[2 + tamanho :]
    return achados


@pytest.fixture
async def tv(monkeypatch):
    """A television already listening, with the driver aimed at the port it took."""
    ligadas: list[_Tv] = []

    async def abrir(cadastro=None, **extra):
        servidor = _Tv(**extra)
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(samsung_legado, "PORTA", servidor.endereco[1])
        monkeypatch.setattr(samsung_legado, "PAREAMENTO_S", 0.5)
        monkeypatch.setattr(samsung_legado, "RESPOSTA_S", 0.5)
        return servidor, SamsungLegado(cadastro or _Cadastro())

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the television to read what was written to it, on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido_e_nao_promete_volume_nem_mudo():
    """This protocol reads nothing back, so a level and a mute would be buttons that lie."""
    validar(SamsungLegado.MANIFESTO)
    assert SamsungLegado.MANIFESTO.auth == Auth.POPUP_NO_APARELHO
    assert "volume" not in SamsungLegado.MANIFESTO.capacidades
    assert "mudo" not in SamsungLegado.MANIFESTO.capacidades
    assert {"mais", "menos"} <= set(SamsungLegado.MANIFESTO.teclas)


async def test_um_poll_e_a_propria_conexao_concedida(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume is None
    assert len(servidor.pareamentos) == 1
    await driver.parar()


async def test_o_pedido_leva_o_endereco_e_uma_identidade_estavel(tv):
    """The television keys its permission on this identity, so it can never move."""
    servidor, driver = await tv()
    await driver.atualizar()
    await driver.atualizar()
    endereco, identidade, nome = servidor.pareamentos[0]
    assert endereco == IP
    assert nome == samsung_legado.NOME_DO_CONTROLE
    # Locally administered and not multicast, which is what an address nobody assigned says.
    assert int(identidade.split(":")[0], 16) & 0x03 == 0x02
    assert servidor.pareamentos[1][1] == identidade
    outra = SamsungLegado(_Cadastro(identidade="tv-do-quarto"))
    assert outra._identidade() != identidade
    await driver.parar()


async def test_uma_tecla_vai_em_base64_dentro_do_pacote(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    await _ate(lambda: servidor.teclas)
    assert servidor.teclas[-1] == "KEY_HOME"
    assert await driver.executar("pausar") is None
    await _ate(lambda: len(servidor.teclas) == 2)
    assert servidor.teclas[-1] == "KEY_PAUSE"
    assert await driver.executar("tecla", "nao_existe") == "invalid_value"
    await driver.parar()


async def test_desligar_e_a_tecla_de_energia(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    await _ate(lambda: servidor.teclas)
    assert servidor.teclas[-1] == "KEY_POWEROFF"
    assert driver.estado().ligado is False
    await driver.parar()


async def test_ligar_e_o_pacote_magico_e_sem_mac_nao_ha_para_onde(tv, monkeypatch):
    enviados: list[bytes] = []
    monkeypatch.setattr(samsung_legado, "_soprar", enviados.append)
    servidor, driver = await tv()
    assert await driver.executar("ligar") == "invalid_value"
    driver.cadastro.campos[samsung_legado.CAMPO_MAC] = "AA:BB:CC:DD:EE:FF"
    assert await driver.executar("ligar") is None
    assert enviados == [bytes.fromhex("aabbccddeeff")]
    await driver.parar()


async def test_o_aviso_na_tela_e_pareamento_pendente_e_nao_uma_falha(tv):
    servidor, driver = await tv(veredito=samsung_legado.ESPERANDO)
    assert await driver.autenticar() == "aguardando"
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"
    await driver.parar()


async def test_um_aviso_seguido_do_sim_e_pareado(tv):
    """A set of that year sends the popup first and the answer of the person after it."""
    servidor, driver = await tv(avisar=True)
    assert await driver.autenticar() == "pareado"
    await driver.parar()


async def test_uma_recusa_da_tela_falha_o_pareamento(tv):
    servidor, driver = await tv(veredito=samsung_legado.NEGADO)
    assert await driver.autenticar() == "falhou"
    await driver.parar()


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()
