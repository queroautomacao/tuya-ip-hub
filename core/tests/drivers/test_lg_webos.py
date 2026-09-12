# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The LG webOS TV against a simulated TV that speaks the SSAP WebSocket.

a driver is tested against a fake device and never against a panel on a bench. The
TV simulated here answers the hello, the register with its dialog, the questions of the poll
and the second socket the navigation keys need, so every test states what left this hub on the
wire and not only what it read back.
"""

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Self

import pytest
from aiohttp import WSMsgType, web

from iphub.drivers.base import CODIGOS, DETALHES
from iphub.drivers.manifesto import TECLAS as VOCABULARIO_DE_TECLAS
from iphub.drivers.manifesto import Auth, TipoCampo, por_lista, validar
from iphub.drivers.nativos import lg_webos
from iphub.drivers.nativos.lg_webos import LgWebos
from iphub.drivers.simulado import ServidorDatagrama, ServidorLinha

UUID = "b1e2c3d4-5566-7788-99aa-bbccddeeff00"
OUTRO_UUID = "00ffeedd-ccbb-aa99-8877-665544332211"
CHAVE = "0123456789abcdef0123456789abcdef"
CHAVE_NOVA = "fedcba9876543210fedcba9876543210"
MAC = "3C:CD:93:11:22:33"

CAMINHO_REMOTO = "/resources/remoto/netinput.pointer.sock"

# The uris of the protocol, written whole here on purpose: this file is what says the driver
# still speaks what the TV listens to, so a constant renamed in the driver must break a test.
SISTEMA = "ssap://system/getSystemInfo"
FIRMWARE = "ssap://com.webos.service.update/getCurrentSWInformation"
LIGAR = "ssap://system/turnOn"
DESLIGAR = "ssap://system/turnOff"
ENERGIA = "ssap://com.webos.service.tvpower/power/getPowerState"
VOLUME = "ssap://audio/getVolume"
DEFINIR_VOLUME = "ssap://audio/setVolume"
MAIS = "ssap://audio/volumeUp"
MENOS = "ssap://audio/volumeDown"
AUDIO = "ssap://audio/getStatus"
MUDO = "ssap://audio/setMute"
APLICATIVO = "ssap://com.webos.applicationManager/getForegroundAppInfo"
APLICATIVOS = "ssap://com.webos.applicationManager/listLaunchPoints"
ABRIR = "ssap://system.launcher/launch"
ENTRADAS = "ssap://tv/getExternalInputList"
TROCAR_ENTRADA = "ssap://tv/switchInput"
CANAL_MAIS = "ssap://tv/channelUp"
CANAL_MENOS = "ssap://tv/channelDown"
CANAL = "ssap://tv/getCurrentChannel"
SAIDA = "ssap://com.webos.service.apiadapter/audio/getSoundOutput"
TROCAR_SAIDA = "ssap://com.webos.service.apiadapter/audio/changeSoundOutput"
MIDIA = "ssap://com.webos.media/getForegroundAppInfo"
TOCAR = "ssap://media.controls/play"
PAUSAR = "ssap://media.controls/pause"
PARAR = "ssap://media.controls/stop"
PONTEIRO = "ssap://com.webos.service.networkinput/getPointerInputSocket"
TELA_DESLIGADA = "ssap://com.webos.service.tvpower/power/turnOffScreen"
TELA_LIGADA = "ssap://com.webos.service.tvpower/power/turnOnScreen"

# What the handshake asks for, which every other assertion of the wire leaves out.
APRESENTACAO = (SISTEMA, FIRMWARE)

APP_HDMI2 = "com.webos.app.hdmi2"
APP_TV_ABERTA = "com.webos.app.livetv"

ERRO_404 = "404 no such service or method"

# The register is the ONE message a real TV refuses when it comes malformed, and a pairing that
# never concludes looks exactly like a TV that is off, so the body is written out
# here whole: a permission dropped from the driver has to break a test and not an installation.
PERMISSOES = [
    "APP_TO_APP",
    "CLOSE",
    "CONTROL_AUDIO",
    "CONTROL_DISPLAY",
    "CONTROL_INPUT_JOYSTICK",
    "CONTROL_INPUT_MEDIA_PLAYBACK",
    "CONTROL_INPUT_MEDIA_RECORDING",
    "CONTROL_INPUT_TEXT",
    "CONTROL_INPUT_TV",
    "CONTROL_MOUSE_AND_KEYBOARD",
    "CONTROL_POWER",
    "CONTROL_TV_SCREEN",
    "LAUNCH",
    "LAUNCH_WEBAPP",
    "READ_APP_STATUS",
    "READ_COUNTRY_INFO",
    "READ_CURRENT_CHANNEL",
    "READ_INPUT_DEVICE_LIST",
    "READ_INSTALLED_APPS",
    "READ_LGE_SDX",
    "READ_LGE_TV_INPUT_EVENTS",
    "READ_NETWORK_STATE",
    "READ_NOTIFICATIONS",
    "READ_POWER_STATE",
    "READ_RUNNING_APPS",
    "READ_SETTINGS",
    "READ_TV_CHANNEL_LIST",
    "READ_TV_CURRENT_TIME",
    "READ_UPDATE_INFO",
    "SEARCH",
    "TEST_OPEN",
    "TEST_PROTECTED",
    "TEST_SECURE",
    "UPDATE_FROM_REMOTE_APP",
    "WRITE_NOTIFICATION_ALERT",
    "WRITE_NOTIFICATION_TOAST",
    "WRITE_SETTINGS",
]


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = UUID
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=lambda: {"mac": MAC})
    segredos: dict[str, str] = field(default_factory=lambda: {"chave_cliente": CHAVE})
    listas: dict[str, tuple] = field(default_factory=dict)


def _respostas(trocas: dict[str, dict] | None = None) -> dict[str, dict]:
    """What a TV that is on and showing HDMI 2 answers to every question of the driver."""
    respostas = {
        SISTEMA: {"modelName": "OLED55C1"},
        FIRMWARE: {"major_ver": "04", "minor_ver": "70.55"},
        ENERGIA: {"state": "Active"},
        VOLUME: {"volumeStatus": {"volume": 37, "muteStatus": False}},
        AUDIO: {"mute": False},
        APLICATIVO: {"appId": APP_HDMI2},
        ENTRADAS: {
            "devices": [
                {"id": "HDMI_1", "label": "Blu-ray", "appId": "com.webos.app.hdmi1"},
                {"id": "HDMI_2", "label": "Receiver", "appId": APP_HDMI2},
            ]
        },
        APLICATIVOS: {
            "launchPoints": [
                {"id": "netflix", "title": "Netflix"},
                {"id": APP_TV_ABERTA, "title": "Live TV"},
            ]
        },
        SAIDA: {"soundOutput": "tv_speaker"},
        MIDIA: {"foregroundAppInfo": [{"playState": "playing"}]},
        CANAL: {"channelName": "Globo"},
        DEFINIR_VOLUME: {},
        MUDO: {},
        MAIS: {},
        MENOS: {},
        CANAL_MAIS: {},
        CANAL_MENOS: {},
        TROCAR_ENTRADA: {},
        ABRIR: {},
        TROCAR_SAIDA: {},
        TOCAR: {},
        PAUSAR: {},
        PARAR: {},
        LIGAR: {},
        DESLIGAR: {},
        TELA_DESLIGADA: {},
        TELA_LIGADA: {},
    }
    respostas.update(trocas or {})
    return respostas


class TvWebos:
    """A simulated LG TV: one WebSocket that speaks JSON and a second one that speaks lines.

    The register answers the key when the driver sent the one this TV knows, and otherwise
    puts the dialog on the screen, which only ends when the test presses aceitar, exactly as a
    person walking to the television does.
    """

    def __init__(
        self,
        respostas: dict[str, dict] | None = None,
        *,
        uuid: str = UUID,
        chave: str = CHAVE,
        chave_nova: str | None = None,
        recusa_dialogo: bool = False,
        mudas: tuple[str, ...] = (),
        socket_remoto: str | None = None,
        atraso_s: float = 0.0,
    ) -> None:
        self.respostas = _respostas() if respostas is None else dict(respostas)
        self.uuid = uuid
        self.chave = chave
        self.chave_nova = chave_nova or chave
        self.recusa_dialogo = recusa_dialogo
        self.mudas = set(mudas)
        self.socket_remoto = socket_remoto
        # A TV that answers every question, just slowly, which is what a budget is measured on.
        self.atraso_s = atraso_s
        self.recebidas: list[dict] = []
        self.botoes: list[str] = []
        self.conexoes = 0
        self.remotas = 0
        self.registros = 0
        self.aceitar = asyncio.Event()
        self.endereco: tuple[str, int] = ("", 0)
        self._runner: web.AppRunner | None = None
        self._sockets: list[web.WebSocketResponse] = []
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self) -> tuple[str, int]:
        app = web.Application()
        app.router.add_route("GET", "/", self._principal)
        app.router.add_route("GET", CAMINHO_REMOTO, self._remoto)
        self._runner = web.AppRunner(app, shutdown_timeout=2.0)
        await self._runner.setup()
        sitio = web.TCPSite(self._runner, "127.0.0.1", 0)
        await sitio.start()
        self.endereco = tuple(self._runner.addresses[0][:2])
        return self.endereco

    async def parar(self) -> None:
        runner, self._runner = self._runner, None
        tarefas = tuple(self._tarefas)
        self._tarefas.clear()
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        await self.derrubar()
        if runner is not None:
            await runner.cleanup()

    async def derrubar(self) -> None:
        """Drops every socket, which is what a TV that rebooted does to the hub."""
        for ws in tuple(self._sockets):
            with suppress(Exception):
                await ws.close()
        self._sockets.clear()

    async def __aenter__(self) -> Self:
        await self.iniciar()
        return self

    async def __aexit__(self, *_erro: object) -> None:
        await self.parar()

    @property
    def porta(self) -> int:
        return self.endereco[1]

    def pedidos(self) -> list[tuple[str, object]]:
        """Every request that arrived, minus the two questions of the handshake."""
        return [
            (documento.get("uri"), documento.get("payload"))
            for documento in self.recebidas
            if documento.get("type") == "request" and documento.get("uri") not in APRESENTACAO
        ]

    def tipos(self) -> list[str]:
        return [str(documento.get("type")) for documento in self.recebidas]

    async def _principal(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        self.conexoes += 1
        self._sockets.append(ws)
        async for mensagem in ws:
            if mensagem.type is not WSMsgType.TEXT:
                continue
            documento = json.loads(mensagem.data)
            self.recebidas.append(documento)
            await self._responder(ws, documento)
        return ws

    async def _remoto(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=8 * 1024)
        await ws.prepare(request)
        self.remotas += 1
        self._sockets.append(ws)
        async for mensagem in ws:
            if mensagem.type is WSMsgType.TEXT:
                self.botoes.append(mensagem.data)
        return ws

    async def _responder(self, ws: web.WebSocketResponse, documento: dict) -> None:
        tipo = documento.get("type")
        identificador = documento.get("id")
        if tipo == "hello":
            # Measured on a webOS 6.5.3 at the bench, the answer to the hello carries NO
            # id, while every other answer of the protocol echoes the id of the question. The
            # simulated TV echoed it, so the suite was green while the real television failed
            # to pair in two seconds and showed nothing on its screen. It answers like the
            # television now, which is the only way this test is worth running.
            await _falar(ws, {"type": "hello", "payload": self._ola()})
            return
        if tipo == "register":
            await self._registrar(ws, documento)
            return
        if tipo != "request":
            return
        uri = str(documento.get("uri"))
        if uri in self.mudas:
            return
        if self.atraso_s:
            await asyncio.sleep(self.atraso_s)
        if uri == PONTEIRO:
            await _falar(ws, _resposta(identificador, {"socketPath": self._remoto_url()}))
            return
        carga = self.respostas.get(uri)
        if carga is None:
            await _falar(ws, {"type": "error", "id": identificador, "error": ERRO_404})
            return
        await _falar(ws, _resposta(identificador, carga))

    def _ola(self) -> dict:
        return {"deviceUUID": self.uuid, "productName": "webOS.TV"}

    def _remoto_url(self) -> str:
        if self.socket_remoto is not None:
            return self.socket_remoto
        return f"ws://127.0.0.1:{self.porta}{CAMINHO_REMOTO}"

    async def _registrar(self, ws: web.WebSocketResponse, documento: dict) -> None:
        self.registros += 1
        identificador = documento.get("id")
        carga = documento.get("payload") or {}
        if self.chave and carga.get("client-key") == self.chave:
            await _falar(ws, _registrado(identificador, self.chave_nova))
            return
        # The dialog is on the screen now, and the TV says nothing else until somebody answers.
        await _falar(ws, _resposta(identificador, {"pairingType": "PROMPT"}))
        tarefa = asyncio.create_task(self._dialogo(ws, identificador))
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def _dialogo(self, ws: web.WebSocketResponse, identificador: object) -> None:
        await self.aceitar.wait()
        if self.recusa_dialogo:
            await _falar(ws, {"type": "error", "id": identificador, "error": "rejected pairing"})
            return
        await _falar(ws, _registrado(identificador, self.chave_nova))


def _resposta(identificador: object, carga: dict) -> dict:
    return {"type": "response", "id": identificador, "payload": {"returnValue": True, **carga}}


def _registrado(identificador: object, chave: str) -> dict:
    return {"type": "registered", "id": identificador, "payload": {"client-key": chave}}


async def _falar(ws: web.WebSocketResponse, documento: dict) -> None:
    with suppress(Exception):
        await ws.send_str(json.dumps(documento))


@pytest.fixture
async def tv(monkeypatch):
    """A driver aimed at the port of a simulated TV, closed when the test ends."""
    criados: list[LgWebos] = []

    def montar(
        simulada: TvWebos | None = None,
        *,
        portas: tuple[int, ...] | None = None,
        cadastro: _Cadastro | None = None,
    ) -> LgWebos:
        if simulada is not None:
            monkeypatch.setattr(lg_webos, "PORTAS", portas or (simulada.porta,))
        driver = LgWebos(cadastro or _Cadastro())
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


async def _parear(driver: LgWebos, simulada: TvWebos) -> str:
    """Presses accept on the TV and collects the answer, the way the panel re-enters."""
    simulada.aceitar.set()
    resultado = "aguardando"
    for _ in range(100):
        await asyncio.sleep(0.01)
        resultado = await driver.autenticar()
        if resultado != "aguardando":
            break
    return resultado


def test_o_manifesto_e_o_de_uma_tv_e_nao_promete_faixa_seguinte():
    """A TV switches, sets a level, picks an input, an app and a sound output, and
    sends keys; there is no next track in the protocol, so it is not declared.
    """
    manifesto = LgWebos.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "tv_lg_webos"
    assert manifesto.categoria == "tv"
    assert manifesto.nuvem is False
    assert manifesto.auth is Auth.POPUP_NO_APARELHO
    assert manifesto.capacidades == (
        "ligar",
        "desligar",
        "volume",
        "mudo",
        "fonte",
        "atalho",
        "modo",
        "tocar",
        "pausar",
        "parar",
        "tecla",
        "comando_extra",
    )
    # Fast forward is not the next track, and a single play and pause button does not
    # exist on this TV; declaring either would be lying to the app.
    assert "proxima" not in manifesto.capacidades
    assert "anterior" not in manifesto.capacidades
    assert "play_pause" not in manifesto.teclas
    assert "agrupar" not in manifesto.capacidades


def test_a_descoberta_da_tv_nao_colide_com_o_ar_de_nuvem():
    """Two manifests claiming the same signature is a test error, and the LG of the
    cloud claims none at all, because a sweep of the LAN never finds it.
    """
    from iphub.drivers.descoberta import montar
    from iphub.drivers.nativos.denon import Denon
    from iphub.drivers.nativos.lg_thinq import LgThinq

    descoberta = LgWebos.MANIFESTO.descoberta
    assert descoberta.ssdp_fabricantes == ("lg",)
    # The fragment of a maker only reads an answer that already arrived, and two letters
    # would also read the answer of a stranger; the service the TV announces names it exactly.
    assert descoberta.ssdp_st == ("urn:lge-com:service:webos-second-screen:1",)
    assert descoberta.mdns_servicos == ()
    assert LgThinq.MANIFESTO.descoberta.ssdp_fabricantes == ()
    assert LgThinq.MANIFESTO.descoberta.ssdp_st == ()
    assert LgThinq.MANIFESTO.nuvem is True
    # Two manifests claiming the same signature is a test error, not a runtime
    # choice, and this is the plan the sweep is built from.
    plano = montar((LgWebos.MANIFESTO, LgThinq.MANIFESTO, Denon.MANIFESTO))
    assert plano.por_st["urn:lge-com:service:webos-second-screen:1"] == "tv_lg_webos"
    assert dict(plano.fabricantes)["lg"] == "tv_lg_webos"


def test_toda_tecla_declarada_tem_um_caminho_ate_a_tv():
    """The manifest declares the keys the driver SENDS, so a key with nowhere to go
    would be a button on the panel that only ever fails.

    Why the list is written out here: comparing the manifest with the tables it is BUILT from
    states that the code equals itself and cannot fail, so a key deleted from the driver would
    disappear from both sides of the assertion at once. The words are the ones a
    scene and the command channel already carry, so they are the literal.
    """
    esperadas = {
        "mais",
        "menos",
        "canal_mais",
        "canal_menos",
        "cima",
        "baixo",
        "esquerda",
        "direita",
        "ok",
        "voltar",
        "inicio",
        "menu",
        "guia",
        "sair",
        "info",
        *(f"digito_{numero}" for numero in range(10)),
    }
    assert set(LgWebos.MANIFESTO.teclas) == esperadas
    assert len(LgWebos.MANIFESTO.teclas) == len(esperadas)
    # A driver only ever declares words of the vocabulary the whole hub speaks.
    assert esperadas <= set(VOCABULARIO_DE_TECLAS)
    caminhos = set(lg_webos.TECLAS_COM_ENDPOINT) | set(lg_webos.NOMES_DE_TECLA)
    assert caminhos == esperadas
    assert lg_webos.NOMES_DE_TECLA["ok"] == "ENTER"
    assert lg_webos.NOMES_DE_TECLA["digito_7"] == "7"
    # The four with an endpoint of their own spare a TV the cost of the second socket.
    assert set(lg_webos.TECLAS_COM_ENDPOINT) == {"mais", "menos", "canal_mais", "canal_menos"}


def test_o_cadastro_pede_o_mac_e_guarda_a_chave_como_segredo():
    """The client-key is a credential of the device, so it lives in the secrets and
    never goes back to the panel; the MAC is what turns a TV on and is required.
    """
    campos = {campo.nome: campo for campo in LgWebos.MANIFESTO.config_campos}
    assert set(campos) == {"mac", "chave_cliente"}
    assert campos["mac"].obrigatorio is True
    assert campos["chave_cliente"].tipo is TipoCampo.SEGREDO
    sugeridas = por_lista(LgWebos.MANIFESTO)
    assert set(sugeridas) == {"entradas", "atalhos", "modos"}
    assert [item.valor for item in sugeridas["entradas"]][:2] == ["HDMI_1", "HDMI_2"]
    assert sugeridas["atalhos"][0].valor == APP_TV_ABERTA


async def test_a_identidade_e_o_uuid_do_hello(monkeypatch):
    """The identity is a uuid and never the address, and this TV answers it before
    any pairing, so a finding of the sweep becomes a registration nobody types.
    """
    async with TvWebos() as simulada:
        monkeypatch.setattr(lg_webos, "PORTAS", (simulada.porta,))
        assert await LgWebos.identificar("127.0.0.1") == UUID
        assert await LgWebos.identificar("tv.local") is None
        # Asking who it is never pairs and never lights the screen.
        assert simulada.registros == 0
    assert await LgWebos.identificar("127.0.0.1") is None


async def test_um_poll_le_energia_volume_mudo_entrada_aplicativo_e_som(tv):
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.volume, estado.mudo) == (True, 37, False)
    # The input on the screen is the one whose app answers for it right now.
    assert estado.fonte == "HDMI_2"
    assert estado.fontes == ("HDMI_1", "HDMI_2")
    assert estado.modo == "tv_speaker"
    assert estado.reproduzindo is True
    assert estado.detalhe == ""
    assert estado.detalhe in ("", *DETALHES)


async def test_o_getsysteminfo_vem_antes_do_registro(tv):
    """A new firmware refuses to register without this question, and asking it
    after the register is what makes a recent TV never connect.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        pedidas = [
            documento.get("uri") or documento.get("type") for documento in simulada.recebidas
        ]
    assert pedidas[:4] == ["hello", SISTEMA, "register", FIRMWARE]


@pytest.mark.parametrize(
    ("carga", "esperado"),
    [
        ({"volumeStatus": {"volume": 37}}, 37),
        ({"volume": 12}, 12),
        ({"volume": 240}, 100),
        ({"volume": "alto"}, None),
        ({"volume": True}, None),
        ({}, None),
    ],
)
async def test_o_volume_vem_aninhado_ou_solto_conforme_o_firmware(tv, carga, esperado):
    """The level is under volumeStatus on some firmwares and loose on others, and
    reading only one of the two breaks on half of the TVs.
    """
    async with TvWebos(_respostas({VOLUME: carga})) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    assert driver.estado().volume == esperado


@pytest.mark.parametrize(
    ("estado", "ligado"),
    [
        ("Active", True),
        ("Screen Off", True),
        ("Power Off", False),
        ("Suspend", False),
        ("Active Standby", False),
    ],
)
async def test_a_tela_apagada_ainda_e_uma_tv_ligada(tv, estado, ligado):
    """The TV with the screen off is on, and only the sleeping states are off."""
    async with TvWebos(_respostas({ENERGIA: {"state": estado}})) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    assert driver.estado().ligado is ligado


async def test_um_firmware_sem_estado_de_energia_e_lido_pelo_aplicativo(tv):
    """A TV old enough to have no power state is on when something is on its screen."""
    respostas = _respostas()
    del respostas[ENERGIA]
    async with TvWebos(respostas) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    assert driver.estado().ligado is True


async def test_o_transporte_e_o_titulo_sao_fatos_diferentes(tv):
    """Only some apps publish a playState, so a TV that does not say leaves the
    transport None, and the title is read from where it really is.
    """
    calada = _respostas(
        {
            MIDIA: {"foregroundAppInfo": [{"playState": ""}]},
            APLICATIVO: {"appId": "netflix"},
        }
    )
    async with TvWebos(calada) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    assert driver.estado().reproduzindo is None
    assert driver.estado().tocando == "Netflix"
    assert driver.estado().fonte is None


async def test_o_titulo_na_tv_aberta_e_o_canal(tv):
    """The channel is the title of what is playing on the open channels, and the name of the
    app everywhere else.
    """
    aberta = _respostas({APLICATIVO: {"appId": APP_TV_ABERTA}})
    async with TvWebos(aberta) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().tocando == "Globo"
        assert (CANAL, {}) in simulada.pedidos()


async def test_as_listas_vazias_de_uma_tv_apagada_nao_apagam_as_do_painel(tv):
    """The inputs and the apps come back EMPTY while the TV is off, and a driver
    that zeroed them each poll would wipe the lists of the equipment every night.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().fontes == ("HDMI_1", "HDMI_2")
        simulada.respostas[ENTRADAS] = {"devices": []}
        simulada.respostas[APLICATIVOS] = {"launchPoints": []}
        simulada.respostas[APLICATIVO] = {"appId": "netflix"}
        await driver.atualizar()
    assert driver.estado().fontes == ("HDMI_1", "HDMI_2")
    assert driver.estado().tocando == "Netflix"


async def test_uma_tv_que_nao_responde_mais_e_offline_depois_de_dois_polls(tv):
    """A TV that is off refuses the connection, which is the normal state of a TV
    that is off; one lost poll keeps the last state and two in a row is offline.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is True, "one lost poll keeps the last state"
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is False
    assert estado.detalhe == "eq_offline"
    # The WebSocket server of the TV dies with the screen, so a refused connection IS the
    # power state; the lists stay, because they are what the panel draws the equipment with.
    assert estado.ligado is False
    assert (estado.volume, estado.mudo, estado.tocando) == (None, None, None)
    # The input on the screen and the sound output are facts of the now, like the level,
    # and a dark TV has neither; leaving them would keep the DP 146 and the DP 147
    # reporting the input and the sound mode of a television that is off. The LISTS are another
    # thing and they stay, because they are what the panel draws the equipment with.
    assert (estado.fonte, estado.modo) == (None, None)
    assert estado.fontes == ("HDMI_1", "HDMI_2")


async def test_uma_tv_que_emudece_no_meio_do_poll_nao_segura_o_prazo(tv, monkeypatch):
    """A TV that accepts the socket and stops answering must not hold the deadline of the
    gestor, so the question has a deadline of its own and the poll gives up on the socket.
    """
    monkeypatch.setattr(lg_webos, "RESPOSTA_S", 0.2)
    monkeypatch.setattr(lg_webos, "ORCAMENTO_DO_POLL_S", 0.5)
    async with TvWebos(mudas=(ENERGIA,)) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_uma_tv_que_responde_tudo_devagar_nao_e_uma_tv_offline(tv, monkeypatch):
    """The budget of the poll and the deadline of the TV are two different reasons to run out
    of time, and only the second is a TV that went away.

    Why it matters: a TV that answers every question, just slowly, spends the budget in the
    middle of one of them; reading that as offline would throw the socket away, count a failure
    and publish no state at all for a television that is answering everything correctly.
    """
    monkeypatch.setattr(lg_webos, "RESPOSTA_S", 1.0)
    monkeypatch.setattr(lg_webos, "ORCAMENTO_DO_POLL_S", 0.5)
    async with TvWebos(atraso_s=0.2) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        await driver.atualizar()
        # The budget really did cut the poll: what is playing is the last question of it, and
        # the level moved to the front because the sound output rides inside its answer.
        assert (MIDIA, {}) not in simulada.pedidos()
    estado = driver.estado()
    assert estado.online is True
    assert estado.detalhe == ""
    # What the two questions that fitted answered is published, and nothing is invented.
    assert estado.ligado is True
    assert (estado.volume, estado.mudo, estado.fontes) == (None, None, ())


async def test_uma_falha_na_ultima_pergunta_do_poll_conta_ate_o_teto(tv, monkeypatch):
    """The title is the last question of the poll, and a failure that lives exactly there must
    count like any other, or the ceiling is never reached.

    Why it matters: with the counter cleared before the last question, a TV whose socket died
    on getCurrentChannel goes back to one failure at every poll, never reaches the ceiling, and
    stays published as a TV that is on and playing while the socket is dead.
    """
    monkeypatch.setattr(lg_webos, "RESPOSTA_S", 0.2)
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True
        simulada.respostas[APLICATIVO] = {"appId": APP_TV_ABERTA}
        simulada.mudas.add(CANAL)
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is False
    assert estado.detalhe == "eq_offline"
    assert estado.reproduzindo is None


async def test_quem_aceita_a_porta_e_nao_fala_nao_segura_o_driver(tv, monkeypatch):
    """Anything on the LAN can hold this port open and say nothing, and the deadline
    of the client covers reaching the socket and not the upgrade that follows it.
    """
    monkeypatch.setattr(lg_webos, "CONEXAO_S", 0.2)
    async with ServidorLinha({}) as mudo:
        monkeypatch.setattr(lg_webos, "PORTAS", (mudo.endereco[1],))
        driver = tv()
        comecou = asyncio.get_running_loop().time()
        await driver.atualizar()
        await driver.atualizar()
        gastou = asyncio.get_running_loop().time() - comecou
        assert mudo.conexoes == 2
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    assert gastou < 2.0


async def test_uma_resposta_gigante_ou_sem_os_campos_nao_derruba_o_poll(tv):
    """A TV with hundreds of apps and a firmware that answers empty objects are both ordinary,
    and neither may cost the poll.
    """
    enorme = {"launchPoints": [{"id": f"app{n}", "title": "x" * 20} for n in range(2_000)]}
    vazios = _respostas(
        {
            APLICATIVOS: enorme,
            ENERGIA: {},
            VOLUME: {},
            AUDIO: {},
            APLICATIVO: {},
            ENTRADAS: {},
            SAIDA: {},
            MIDIA: {},
        }
    )
    async with TvWebos(vazios) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.volume, estado.mudo, estado.fonte) == (None, None, None, None)
    assert (estado.modo, estado.reproduzindo, estado.tocando) == (None, None, None)
    assert estado.fontes == ()


def test_a_lista_de_aplicativos_de_uma_tv_tem_teto():
    """A TV with hundreds of apps must not make the daemon carry all of them forever."""
    enorme = {"launchPoints": [{"id": f"app{n}", "title": "x"} for n in range(5_000)]}
    assert len(lg_webos._aplicativos_de(enorme)) == lg_webos.APLICATIVOS_MAXIMO
    quebrada = {"launchPoints": ["x", {"sem": "id"}, {"id": "netflix", "title": "Netflix"}]}
    assert lg_webos._aplicativos_de(quebrada) == {"netflix": "Netflix"}
    assert lg_webos._aplicativos_de({"launchPoints": "nada"}) == {}
    assert lg_webos._entradas_de({"devices": "nada"}) == ()


async def test_ligar_sopra_o_pacote_magico_no_broadcast(tv, monkeypatch):
    """The TV answers nothing over IP while it is off, so power on is the magic
    packet to the MAC of the registration, sent more than once.
    """
    assert lg_webos.PORTAS_WOL == (9, 7)
    async with ServidorDatagrama({}) as rede, ServidorDatagrama({}) as outra:
        monkeypatch.setattr(lg_webos, "ENDERECO_WOL", "127.0.0.1")
        monkeypatch.setattr(lg_webos, "PORTAS_WOL", (rede.endereco[1], outra.endereco[1]))
        async with TvWebos() as simulada:
            driver = tv(simulada)
            assert await driver.executar("ligar") is None
            await asyncio.sleep(0.05)
            # Why nothing else left: nobody has paired this TV and no socket is open, so the
            # magic packet is the whole of turning it on; the turnOn of an open socket is the
            # test below and needs a poll before it.
            assert (LIGAR, {}) not in simulada.pedidos()
        # The packet leaves on BOTH ports a magic packet is listened for on, and
        # on every address that can carry it to the set, so the count is one send per
        # destination that lands on this socket and never fewer than the sends themselves.
        assert len(rede.recebidos) >= lg_webos.ENVIOS_WOL
        assert len(rede.recebidos) % lg_webos.ENVIOS_WOL == 0
        assert len(outra.recebidos) == len(rede.recebidos)
    pacote = rede.recebidos[0]
    assert pacote == b"\xff" * 6 + bytes.fromhex("3ccd93112233") * 16
    assert set(rede.recebidos) == {pacote}
    assert outra.recebidos == rede.recebidos
    assert driver.estado().ligado is True


async def test_o_turnon_pega_carona_no_socket_que_ja_esta_aberto(tv, monkeypatch):
    """A TV in the active standby of Quick Start+ keeps the socket alive, and there the turnOn
    is what lights the screen.
    """
    async with ServidorDatagrama({}) as rede:
        monkeypatch.setattr(lg_webos, "ENDERECO_WOL", "127.0.0.1")
        monkeypatch.setattr(lg_webos, "PORTAS_WOL", (rede.endereco[1],))
        async with TvWebos() as simulada:
            driver = tv(simulada)
            await driver.atualizar()
            assert await driver.executar("ligar") is None
            assert (LIGAR, {}) in simulada.pedidos()


@pytest.mark.parametrize("mac", ["", "3C:CD:93:11:22", "nao é um mac", "zz:cd:93:11:22:33"])
async def test_um_cadastro_sem_mac_legivel_nunca_sopra_nada(tv, monkeypatch, mac):
    async with ServidorDatagrama({}) as rede:
        monkeypatch.setattr(lg_webos, "ENDERECO_WOL", "127.0.0.1")
        monkeypatch.setattr(lg_webos, "PORTAS_WOL", (rede.endereco[1],))
        async with TvWebos() as simulada:
            driver = tv(simulada, cadastro=_Cadastro(campos={"mac": mac}))
            assert await driver.executar("ligar") == "invalid_value"
            await asyncio.sleep(0.05)
        assert rede.recebidos == []


async def test_desligar_numa_tv_ja_apagada_nao_manda_nada(tv):
    """TurnOff on a TV that is already off TURNS IT ON, so the scene of leaving
    the house must never send it to a television that is already dark.
    """
    apagada = _respostas({ENERGIA: {"state": "Power Off"}})
    async with TvWebos(apagada) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().ligado is False
        antes = len(simulada.pedidos())
        assert await driver.executar("desligar") is None
        assert len(simulada.pedidos()) == antes


async def test_desligar_manda_o_turnoff_e_nao_espera_resposta(tv):
    """A TV that is shutting down answers the turnOff unreliably, and waiting for
    an answer spends the whole deadline on every power off.
    """
    async with TvWebos(mudas=(DESLIGAR,)) as simulada:
        driver = tv(simulada)
        assert await driver.executar("desligar") is None
        await asyncio.sleep(0.05)
        assert simulada.pedidos() == [(DESLIGAR, {})]
    assert driver.estado().ligado is False


async def test_o_volume_vai_na_escala_da_secao_6(tv):
    """The scale of the TV is already the 0 to 100, so nothing is converted."""
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("volume", 0) is None
        assert await driver.executar("volume", 40) is None
        assert await driver.executar("volume", 100) is None
        assert simulada.pedidos() == [
            (DEFINIR_VOLUME, {"volume": 0}),
            (DEFINIR_VOLUME, {"volume": 40}),
            (DEFINIR_VOLUME, {"volume": 100}),
        ]
    assert driver.estado().volume == 100


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_a_tv(tv, valor):
    """True is an int in Python: a mute arriving where a volume fits would silence a room."""
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("volume", valor) == "invalid_value"
        assert simulada.pedidos() == []


@pytest.mark.parametrize("saida", ["lineout", "external_speaker"])
async def test_o_nivel_e_recusado_onde_a_tv_o_engole_calada(tv, saida):
    """On the line output and on an external speaker the TV takes an absolute
    level and does nothing with it, so a bar the TV never moved is refused instead.
    """
    async with TvWebos(_respostas({SAIDA: {"soundOutput": saida}})) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        antes = len(simulada.pedidos())
        assert await driver.executar("volume", 30) == "nao_suportado"
        assert len(simulada.pedidos()) == antes


async def test_na_saida_de_linha_a_tv_nao_reporta_volume_nem_mudo(tv):
    """With the sound going out on the line output the TV has no level of its own, and an
    empty bar is the honest panel.
    """
    async with TvWebos(_respostas({SAIDA: {"soundOutput": "lineout"}})) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert (driver.estado().volume, driver.estado().mudo) == (None, None)
        assert await driver.executar("mudo", True) == "nao_suportado"


async def test_um_getsoundoutput_que_falhou_nao_reabre_o_nivel_que_a_tv_engole(tv):
    """The sound output is the guard that refuses a level the TV would swallow in silence, so a
    question that failed keeps the last word of the TV instead of clearing it.

    Why it matters: clearing it opens exactly the path the driver says it closes, the level
    goes to a TV on the line output and is swallowed there, and the mode of the equipment
    blinks to None, which is a report that says nothing happened.
    """
    async with TvWebos(_respostas({SAIDA: {"soundOutput": "lineout"}})) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().modo == "lineout"
        assert await driver.executar("volume", 30) == "nao_suportado"
        # This firmware stops answering that one question, which is a 404 and not a TV that went.
        del simulada.respostas[SAIDA]
        await driver.atualizar()
        assert driver.estado().online is True
        assert driver.estado().modo == "lineout"
        antes = len(simulada.pedidos())
        assert await driver.executar("volume", 30) == "nao_suportado"
        assert len(simulada.pedidos()) == antes


async def test_o_mudo_e_absoluto_e_nao_alterna(tv):
    """The endpoint takes the state, so the driver never has to guess where it stood."""
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
        assert simulada.pedidos() == [(MUDO, {"mute": True}), (MUDO, {"mute": False})]
        assert await driver.executar("mudo", "sim") == "invalid_value"
    assert driver.estado().mudo is False


async def test_entrada_atalho_e_modo_vao_cada_um_no_campo_do_endpoint_dele(tv):
    """Why they are three capabilities and not one list: each one has exactly one command of
    the wire, so nothing has to guess whether a value is an input or an app.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("fonte", "HDMI_1") is None
        assert await driver.executar("atalho", "netflix") is None
        assert await driver.executar("modo", "external_arc") is None
        assert simulada.pedidos() == [
            (TROCAR_ENTRADA, {"inputId": "HDMI_1"}),
            (ABRIR, {"id": "netflix"}),
            (TROCAR_SAIDA, {"output": "external_arc"}),
        ]
    assert driver.estado().fonte == "HDMI_1"
    assert driver.estado().modo == "external_arc"


@pytest.mark.parametrize("valor", ["", "x" * 80, "HDMI 1", 7, None, "a\nb", "com/webos"])
async def test_um_valor_fora_do_alfabeto_do_fio_nunca_chega_a_tv(tv, valor):
    """The value of a registration lands inside a message on the wire, so what is
    not a word of the TV never leaves this hub.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("atalho", valor) == "invalid_value"
        assert await driver.executar("modo", valor) == "invalid_value"
        assert simulada.pedidos() == []


async def test_o_transporte_fala_os_tres_comandos_separados(tv):
    """Tocar, pausar and parar are three facts, and this protocol has exactly the
    three, so nothing keeps a boolean in memory to guess a single button.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("tocar") is None
        assert await driver.executar("pausar") is None
        assert await driver.executar("parar") is None
        assert simulada.pedidos() == [(TOCAR, {}), (PAUSAR, {}), (PARAR, {})]


async def test_as_teclas_com_endpoint_vao_pelo_socket_principal(tv):
    """Four keys have an endpoint of their own, which spares a TV the second socket."""
    async with TvWebos() as simulada:
        driver = tv(simulada)
        for tecla in ("mais", "menos", "canal_mais", "canal_menos"):
            assert await driver.executar("tecla", tecla) is None
        assert simulada.pedidos() == [(MAIS, {}), (MENOS, {}), (CANAL_MAIS, {}), (CANAL_MENOS, {})]
        assert simulada.remotas == 0


async def test_a_tecla_de_navegacao_abre_o_segundo_socket_e_manda_texto(tv):
    """The navigation keys do not go through the main socket, and the second one
    speaks lines of text and not JSON; it is opened on the first key and reused after that.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "inicio") is None
        assert await driver.executar("tecla", "ok") is None
        assert await driver.executar("tecla", "digito_5") is None
        await asyncio.sleep(0.05)
        assert simulada.botoes == [
            "type:button\nname:HOME\n\n",
            "type:button\nname:ENTER\n\n",
            "type:button\nname:5\n\n",
        ]
        # The address of the remote socket is asked for once and the socket is kept.
        assert simulada.pedidos().count((PONTEIRO, {})) == 1
        assert simulada.remotas == 1


async def test_um_socket_remoto_fora_do_endereco_da_tv_e_recusado(tv):
    """The address of the second socket comes from the device, and following it
    wherever it points would make the hub a proxy into the LAN of the customer.
    """
    async with TvWebos(socket_remoto="ws://10.9.9.9:3000/pointer") as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "ok") == "erro_aparelho"
        assert simulada.remotas == 0


async def test_um_socket_remoto_que_aceita_e_nao_fala_nao_segura_a_tecla(tv, monkeypatch):
    """The address of the second socket is dictated by the device, so whatever
    answers on the port it named must not hold the key for as long as it feels like.
    """
    monkeypatch.setattr(lg_webos, "CONEXAO_S", 0.2)
    async with ServidorLinha({}) as mudo:
        anfitriao, porta = mudo.endereco[:2]
        async with TvWebos(socket_remoto=f"ws://{anfitriao}:{porta}/x") as simulada:
            driver = tv(simulada)
            comecou = asyncio.get_running_loop().time()
            # The deadline of the client covers reaching the socket and not the upgrade that
            # follows it, so without one of its own this call never comes back.
            async with asyncio.timeout(3.0):
                assert await driver.executar("tecla", "ok") == "eq_offline"
            gastou = asyncio.get_running_loop().time() - comecou
            assert mudo.conexoes == 1
    assert gastou < 2.0


@pytest.mark.parametrize(
    "endereco",
    [
        "http://127.0.0.1:3000/x",
        "ws://tv.local:3000/x",
        "ws://10.9.9.9:3000/x",
        "ws://127.0.0.1/x",
        "x" * 300,
        "",
        7,
        None,
    ],
)
def test_o_endereco_do_socket_remoto_e_julgado_antes_de_ser_discado(endereco):
    assert lg_webos._url_de_entrada(endereco, "127.0.0.1") is None
    assert lg_webos._url_de_entrada("ws://127.0.0.1:3001/x", "127.0.0.1") == "ws://127.0.0.1:3001/x"


async def test_o_comando_extra_e_um_conjunto_fechado(tv):
    """The extra channel carries a name, so the set of extras is closed here; a free
    uri would be raw passage into a device of the LAN.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("comando_extra", "tela_desligada") is None
        assert await driver.executar("comando_extra", "tela_ligada") is None
        assert await driver.executar("comando_extra", "system/turnOff") == "invalid_value"
        assert await driver.executar("comando_extra", 7) == "invalid_value"
        assert simulada.pedidos() == [(TELA_DESLIGADA, {}), (TELA_LIGADA, {})]


async def test_um_modelo_sem_o_comando_de_tela_e_perguntado_uma_vez_so(tv):
    """The 404 of a function that does not exist is the one error that means
    nao_suportado, and asking again on every scene is a round trip that will never work.
    """
    respostas = _respostas()
    del respostas[TELA_DESLIGADA]
    async with TvWebos(respostas) as simulada:
        driver = tv(simulada)
        assert await driver.executar("comando_extra", "tela_desligada") == "nao_suportado"
        assert await driver.executar("comando_extra", "tela_desligada") == "nao_suportado"
        assert simulada.pedidos() == [(TELA_DESLIGADA, {})]


async def test_um_erro_que_nao_e_o_404_e_aparelho_que_falhou(tv):
    """Every other error of a TV is a device that failed, and reading it as nao_suportado would
    turn a passing fault into a function the hub stops offering.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        simulada.respostas.pop(TROCAR_ENTRADA)
        assert await driver.executar("fonte", "HDMI_1") == "nao_suportado"
    with pytest.raises(lg_webos._Falha) as achado:
        lg_webos._carga({"type": "error", "error": "500 internal server error"})
    assert achado.value.codigo == "erro_aparelho"
    with pytest.raises(lg_webos._Falha) as achado:
        lg_webos._carga({"type": "error", "error": ERRO_404})
    assert achado.value.codigo == "nao_suportado"
    with pytest.raises(lg_webos._Falha) as achado:
        lg_webos._carga({"type": "response", "payload": {"returnValue": False}})
    assert achado.value.codigo == "erro_aparelho"


async def test_sem_chave_nenhum_comando_toca_no_fio(tv):
    """Pairing is explicit, so a poll and a command never register by themselves,
    because registering is what puts the dialog on the screen of the customer.
    """
    async with TvWebos(chave="") as simulada:
        driver = tv(simulada, cadastro=_Cadastro(segredos={}))
        assert await driver.executar("mudo", True) == "auth_pendente"
        await driver.atualizar()
        await driver.atualizar()
        assert simulada.conexoes == 0
        assert simulada.registros == 0
    estado = driver.estado()
    assert estado.online is False
    assert estado.detalhe == "auth_pendente"


async def test_ligar_funciona_numa_tv_que_ninguem_pareou(tv, monkeypatch):
    """Waking a TV is a packet on the LAN and needs no pairing and no socket, which is what
    lets a scene of arriving light a television that was just installed.
    """
    async with ServidorDatagrama({}) as rede:
        monkeypatch.setattr(lg_webos, "ENDERECO_WOL", "127.0.0.1")
        monkeypatch.setattr(lg_webos, "PORTAS_WOL", (rede.endereco[1],))
        async with TvWebos(chave="") as simulada:
            driver = tv(simulada, cadastro=_Cadastro(segredos={}))
            assert await driver.executar("ligar") is None
            await asyncio.sleep(0.05)
            assert simulada.registros == 0
        assert len(rede.recebidos) >= lg_webos.ENVIOS_WOL
        assert len(rede.recebidos) % lg_webos.ENVIOS_WOL == 0


async def test_o_pareamento_devolve_aguardando_e_a_chamada_seguinte_colhe_a_chave(tv):
    """And 14: a person walks to the TV and looks for the remote, which no ten second
    deadline covers, so the pairing answers aguardando and the panel re-enters to collect it.
    """
    async with TvWebos(chave="", chave_nova=CHAVE_NOVA) as simulada:
        driver = tv(simulada, cadastro=_Cadastro(segredos={}))
        assert await driver.autenticar() == "aguardando"
        assert await driver.autenticar() == "aguardando"
        # The dialog on the screen is one, because a second register would put up another.
        assert simulada.registros == 1
        assert await _parear(driver, simulada) == "pareado"
        assert driver.chave_do_aparelho() == CHAVE_NOVA
        # The socket that held the dialog is the one that answers the first command.
        assert await driver.executar("mudo", True) is None
        assert simulada.conexoes == 1


async def test_o_corpo_do_registro_e_o_que_a_tv_aceita(tv):
    """A pairing that never concludes is indistinguishable from a TV that is off,
    which is the most expensive symptom of this driver, and the register is the one message a
    TV refuses when it comes malformed.
    """
    async with TvWebos(chave="") as simulada:
        driver = tv(simulada, cadastro=_Cadastro(segredos={}))
        assert await driver.autenticar() == "aguardando"
        registros = [
            documento for documento in simulada.recebidas if documento.get("type") == "register"
        ]
    # A registration with no key travels with the field PRESENT and null, which is what the TV
    # reads as "show the dialog", and never absent and never an empty string.
    assert registros == [
        {
            "type": "register",
            "id": "register_0",
            "payload": {
                "forcePairing": False,
                "pairingType": "PROMPT",
                "client-key": None,
                "manifest": {
                    "appVersion": "1.1",
                    "manifestVersion": 1,
                    "permissions": PERMISSOES,
                },
            },
        }
    ]


async def test_o_registro_de_um_cadastro_com_chave_leva_a_chave(tv):
    """The same message, with the key of the registration in it, which is what opens a session
    with no dialog on the screen of the customer.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.autenticar() == "pareado"
        registro = next(
            documento for documento in simulada.recebidas if documento.get("type") == "register"
        )
    assert registro["payload"]["client-key"] == CHAVE
    assert registro["payload"]["manifest"]["permissions"] == PERMISSOES


async def test_um_dialogo_recusado_na_tela_e_falhou(tv):
    async with TvWebos(chave="", recusa_dialogo=True) as simulada:
        driver = tv(simulada, cadastro=_Cadastro(segredos={}))
        assert await driver.autenticar() == "aguardando"
        assert await _parear(driver, simulada) == "falhou"
        assert driver.chave_do_aparelho() == ""


async def test_um_pareamento_com_a_chave_do_cadastro_nao_acende_dialogo_nenhum(tv):
    """A registration that already carries the key pairs in one exchange and shows nothing on
    the screen of the customer.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.autenticar() == "pareado"
        assert driver.chave_do_aparelho() == CHAVE


async def test_uma_chave_rotacionada_pela_tv_e_adotada(tv):
    """The TV rotates the key, and a driver that ignored the rotation would lose
    the pairing by itself on the next firmware update.
    """
    async with TvWebos(chave=CHAVE, chave_nova=CHAVE_NOVA) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True
        assert driver.chave_do_aparelho() == CHAVE_NOVA


async def test_uma_chave_que_a_tv_nao_honra_mais_vira_auth_pendente(tv):
    """A TV that asks for the dialog again does not know this key any more, and leaving the
    socket open would leave a dialog on a screen nobody is looking at.
    """
    async with TvWebos(chave="outra-chave-qualquer") as simulada:
        driver = tv(simulada)
        assert await driver.executar("mudo", True) == "auth_pendente"
        await driver.atualizar()
        await driver.atualizar()
        assert simulada.pedidos() == []
    assert driver.estado().detalhe == "auth_pendente"


async def test_um_poll_nao_toma_o_socket_da_pessoa_que_esta_na_frente_da_tv(tv):
    """The dialog on the screen dies with the socket, so a poll landing in the middle of the
    pairing must not take it away from the person walking to the television.
    """
    async with TvWebos(chave="", chave_nova=CHAVE_NOVA) as simulada:
        driver = tv(simulada, cadastro=_Cadastro(segredos={}))
        assert await driver.autenticar() == "aguardando"
        await driver.atualizar()
        assert driver.estado().detalhe == ""
        await driver.atualizar()
        assert driver.estado().detalhe == "auth_pendente"
        assert simulada.conexoes == 1
        assert simulada.registros == 1
        assert await _parear(driver, simulada) == "pareado"


async def test_a_tv_que_trocou_de_identidade_nao_e_comandada(tv):
    """The identity is the uuid and the address is only where it answered today; a
    lease that moved would otherwise leave the hub commanding the TV of the neighbour.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True
        simulada.uuid = OUTRO_UUID
        await simulada.derrubar()
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        # The uuid learned is not forgotten by going offline, or the stranger holding the
        # address would be adopted under the name of this equipment on the next poll.
        await driver.atualizar()
        assert driver.estado().online is False
        assert await driver.executar("mudo", True) == "eq_offline"


async def test_a_porta_certa_e_achada_e_guardada(tv):
    """The TV serves the protocol in the clear on one port and over TLS on another, and no
    field of a registration tells which one this firmware takes.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada, portas=(9, simulada.porta))
        await driver.atualizar()
        assert driver.estado().online is True
        assert simulada.conexoes == 1
        antes = len(simulada.pedidos())
        assert await driver.executar("mudo", True) is None
        # The socket that answered is kept, so a command is one message and not a handshake.
        assert len(simulada.pedidos()) == antes + 1
        assert simulada.conexoes == 1


def test_o_esquema_da_url_muda_com_a_porta():
    """The clear socket on 3000 and the TLS one on 3001, which is the only
    difference between the two doors of the same protocol.
    """
    assert lg_webos.PORTAS == (3000, 3001)
    assert lg_webos._url("192.168.0.9", 3000) == "ws://192.168.0.9:3000"
    assert lg_webos._url("192.168.0.9", 3001) == "wss://192.168.0.9:3001"
    assert lg_webos._url("fe80::1", 3000) == "ws://[fe80::1]:3000"
    # The certificate of the TV is self signed, so validating it refuses every TV.
    assert lg_webos.SEM_CERTIFICADO is False


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(tv):
    """Only an IP literal reaches a device, so the hub is never a resolver."""
    async with TvWebos() as simulada:
        driver = tv(simulada, cadastro=_Cadastro(ip="tv-da-sala"))
        assert await driver.executar("mudo", True) == "eq_offline"
        await driver.atualizar()
        assert simulada.conexoes == 0
    assert driver.estado().online is False


@pytest.mark.parametrize("acao", ["temperatura", "vento", "agrupar", "proxima", "anterior"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(tv, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar(acao, 22) == "nao_suportado"
        assert simulada.pedidos() == []


async def test_uma_tecla_sem_nome_no_protocolo_nao_inventa_um(tv):
    """The gestor already refuses a word outside the manifest, and what gets here without a
    name of the remote is answered and never guessed.
    """
    async with TvWebos() as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "play_pause") == "nao_suportado"
        assert await driver.executar("tecla", 7) == "invalid_value"
        assert simulada.remotas == 0


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """Five stable codes and nothing else ever leaves a driver."""
    do_driver = {
        lg_webos.EQ_OFFLINE,
        lg_webos.INVALID_VALUE,
        lg_webos.ERRO_APARELHO,
        lg_webos.AUTH_PENDENTE,
        lg_webos.NAO_SUPORTADO,
    }
    assert do_driver == set(CODIGOS)
    assert {lg_webos.PAREADO, lg_webos.AGUARDANDO, lg_webos.FALHOU} == set(lg_webos.RESULTADOS)


def test_o_pacote_magico_vai_a_todo_endereco_que_pode_leva_lo():
    """The limited broadcast alone does not cross every network this hub runs on.

    A hub behind a bridge network sends 255.255.255.255 into the bridge and nowhere else,
    while the directed broadcast of the subnet of the equipment is a routable address that
    crosses it; the unicast reaches a set the switch still remembers. An address the
    registration does not carry leaves only the limited broadcast, which is what a daemon on
    the network of the installation needs anyway.
    """
    assert lg_webos._destinos_do_wol("192.0.2.10") == (
        "255.255.255.255",
        "192.0.2.255",
        "192.0.2.10",
    )
    assert lg_webos._destinos_do_wol("") == ("255.255.255.255",)
    assert lg_webos._destinos_do_wol("nao é um endereço") == ("255.255.255.255",)
