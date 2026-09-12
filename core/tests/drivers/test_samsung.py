# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Samsung TV of the Tizen generation, against a simulated TV.

a driver is tested against a fake device and never against hardware, and this one
answers on three wires, so the simulated TV carries the three: the REST door and the socket of
the remote control on one port, the UPnP renderer on another, and a datagram server standing
where the segment is for the magic packet. What every test here asserts is what arrived on the
wire, because this TV confirms nothing and a driver that only read its own answers back would
pass while the room stayed dark.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Self

import pytest
from aiohttp import WSMsgType, web

from iphub.drivers import gestor
from iphub.drivers.base import CODIGOS, RESULTADOS
from iphub.drivers.manifesto import Auth, por_lista, validar
from iphub.drivers.nativos import samsung
from iphub.drivers.nativos.samsung import Samsung
from iphub.drivers.simulado import ServidorDatagrama

HOST_LOCAL = "127.0.0.1"
UUID = "be9554b9-c9fb-41f4-8920-22da015376a4"
MAC = "aabbccddeeff"
TOKEN = "123456789"

# A port nobody listens on, which is what a model with no UPnP renderer looks like.
PORTA_MORTA = 9
PRAZO_DE_PARADA_S = 2.0

EVENTO_CONECTADO = {"event": "ms.channel.connect", "data": {"token": int(TOKEN)}}
EVENTO_NEGADO = {"event": "ms.channel.unauthorized"}
EVENTO_ANTIGO = {
    "event": "ms.error",
    "data": {"message": "unrecognized method value : ms.remote.control"},
}
EVENTOS_DE_RUIDO = ({"event": "ed.edenTV.update"}, {"event": "ms.voiceApp.hide"})

_DESEJADO = re.compile(r"<Desired(?:Volume|Mute)>(-?[0-9]+)</Desired(?:Volume|Mute)>")


def _info(**trocas: object) -> dict:
    """The device info of a real TV, with the fields this driver reads."""
    aparelho = {
        "FrameTVSupport": "false",
        "OS": "Tizen",
        "TokenAuthSupport": "true",
        "PowerState": "on",
        "udn": f"uuid:{UUID}",
        "modelName": "UE43LS003",
        "name": "[TV] Samsung",
        "type": "Samsung SmartTV",
        "wifiMac": "aa:bb:cc:dd:ee:ff",
    }
    aparelho.update(trocas)
    return {
        "device": aparelho,
        "id": f"uuid:{UUID}",
        "type": "Samsung SmartTV",
        "version": "2.0.25",
    }


class TvSimulada:
    """A Tizen TV: the REST door and the socket of the remote control on one port, the UPnP
    renderer on another, both recording what arrived.
    """

    def __init__(
        self,
        *,
        info: object = None,
        abertura: tuple[dict, ...] | None = None,
        volume: int = 25,
        mudo: bool = False,
        renderizador: bool = True,
        estado_rest: int = 200,
        estado_dmr: int = 200,
        demora_do_mudo: float = 0.0,
        demora_do_socket: float = 0.0,
        depois_do_quadro: dict | None = None,
    ) -> None:
        self.info = _info() if info is None else info
        self.abertura = (EVENTO_CONECTADO,) if abertura is None else abertura
        self.volume = volume
        self.mudo = mudo
        self.renderizador = renderizador
        self.estado_rest = estado_rest
        # A renderer that answers, and answers an error, which is not one that is not there.
        self.estado_dmr = estado_dmr
        # A TV on a bad wifi, which is what puts a command inside a poll that is in flight.
        self.demora_do_mudo = demora_do_mudo
        # A TV that accepts the connection and takes its time on the handshake.
        self.demora_do_socket = demora_do_socket
        # What the TV says right after a frame, which is where it rotates the token.
        self.depois_do_quadro = depois_do_quadro
        self.quadros: list[dict] = []
        self.aberturas: list[str] = []
        self.rest: list[str] = []
        self.soap: list[tuple[str, str]] = []
        self.cabecalhos: list[dict[str, str]] = []
        self._runners: list[web.AppRunner] = []
        self._sockets: set[web.WebSocketResponse] = set()
        self.porta = 0
        self.porta_dmr = PORTA_MORTA

    async def __aenter__(self) -> Self:
        aplicacao = web.Application()
        aplicacao.router.add_get(samsung.CAMINHO_REST, self._device_info)
        aplicacao.router.add_get(samsung.CAMINHO_CONTROLE, self._controle)
        self.porta = await self._subir(aplicacao)
        if self.renderizador:
            dmr = web.Application()
            dmr.router.add_post(samsung.CAMINHO_DMR, self._dmr)
            self.porta_dmr = await self._subir(dmr)
        return self

    async def __aexit__(self, *_erro: object) -> None:
        # A socket the driver left open holds the shutdown of the server, and a test that
        # hangs says nothing; the TV of a customer that is unplugged does exactly this.
        for socket in tuple(self._sockets):
            await socket.close()
        for runner in self._runners:
            await runner.cleanup()

    async def _subir(self, aplicacao: web.Application) -> int:
        runner = web.AppRunner(aplicacao, shutdown_timeout=PRAZO_DE_PARADA_S)
        await runner.setup()
        await web.TCPSite(runner, HOST_LOCAL, 0).start()
        self._runners.append(runner)
        return runner.addresses[0][1]

    async def _device_info(self, pedido: web.Request) -> web.Response:
        self.rest.append(pedido.path_qs)
        if self.estado_rest >= 400:
            return web.Response(status=self.estado_rest, text="")
        corpo = self.info if isinstance(self.info, str) else json.dumps(self.info)
        return web.Response(status=200, text=corpo, content_type="application/json")

    async def _controle(self, pedido: web.Request) -> web.WebSocketResponse:
        self.aberturas.append(pedido.path_qs)
        if self.demora_do_socket:
            await asyncio.sleep(self.demora_do_socket)
        socket = web.WebSocketResponse()
        await socket.prepare(pedido)
        self._sockets.add(socket)
        try:
            for evento in self.abertura:
                await socket.send_str(json.dumps(evento))
            async for mensagem in socket:
                if mensagem.type is WSMsgType.TEXT:
                    self.quadros.append(json.loads(mensagem.data))
                    if self.depois_do_quadro is not None:
                        await socket.send_str(json.dumps(self.depois_do_quadro))
        finally:
            self._sockets.discard(socket)
        return socket

    async def _dmr(self, pedido: web.Request) -> web.Response:
        # The renderer routes by the SOAPAction header and not by the path, so a driver
        # that got the header wrong would be answered by nothing at all on a real TV.
        self.cabecalhos.append(dict(pedido.headers))
        acao = pedido.headers.get("SOAPAction", "").strip('"').rsplit("#", 1)[-1]
        corpo = await pedido.text()
        self.soap.append((acao, corpo))
        if self.demora_do_mudo and acao == samsung.PEDE_MUDO:
            await asyncio.sleep(self.demora_do_mudo)
        if self.estado_dmr >= 400:
            return web.Response(status=self.estado_dmr, text="")
        achado = _DESEJADO.search(corpo)
        desejado = int(achado.group(1)) if achado is not None else 0
        if acao == samsung.POE_VOLUME:
            self.volume = desejado
        if acao == samsung.POE_MUDO:
            self.mudo = bool(desejado)
        lido = {
            samsung.PEDE_VOLUME: f"<CurrentVolume>{self.volume}</CurrentVolume>",
            samsung.PEDE_MUDO: f"<CurrentMute>{int(self.mudo)}</CurrentMute>",
        }.get(acao, "")
        return web.Response(status=200, text=_envelope(acao, lido), content_type="text/xml")

    def teclas(self) -> list[str]:
        """The key of every remote control frame that arrived, in order."""
        return [
            quadro["params"]["DataOfCmd"]
            for quadro in self.quadros
            if quadro.get("method") == samsung.METODO_TECLA
        ]

    def comandos(self) -> list[str]:
        return [
            quadro["params"]["Cmd"]
            for quadro in self.quadros
            if quadro.get("method") == samsung.METODO_TECLA
        ]


def _envelope(acao: str, dentro: str) -> str:
    return (
        '<?xml version="1.0"?><s:Envelope '
        'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        f'<u:{acao}Response xmlns:u="{samsung.SERVICO_DMR}">{dentro}</u:{acao}Response>'
        "</s:Body></s:Envelope>"
    )


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = UUID
    ip: str = HOST_LOCAL
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


@pytest.fixture
async def tv(monkeypatch):
    """A simulated TV plus a driver aimed at its ports, closed when the test ends.

    The three constants a test turns are the ports and the two schemes: the simulated TV
    speaks plain HTTP, and the driver only puts the token on the wire it considers the TLS
    one, so a test that did not move the TLS port here could never see the token at all.
    """
    criados: list[Samsung] = []

    def montar(simulada: TvSimulada, cadastro: _Cadastro | None = None, **campos: str) -> Samsung:
        monkeypatch.setattr(samsung, "PORTA_TLS", simulada.porta)
        monkeypatch.setattr(samsung, "PORTAS", (simulada.porta,))
        monkeypatch.setattr(samsung, "ESQUEMA_SEGURO", "http")
        monkeypatch.setattr(samsung, "FIO_SEGURO", "ws")
        # The gap between frames and the hold of the power key are seconds of a real
        # remote control, and a suite that waited them out would pay them on every run.
        monkeypatch.setattr(samsung, "INTERVALO_MINIMO_S", 0.0)
        monkeypatch.setattr(samsung, "SEGUNDOS_SEGURANDO", 0.05)
        monkeypatch.setattr(samsung, "PRAZO_DE_RESPOSTA_S", 0.05)
        if cadastro is None:
            cadastro = _Cadastro(campos={"porta_dmr": str(simulada.porta_dmr), **campos})
        driver = Samsung(cadastro)
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


async def _ate(condicao, prazo_s: float = 2.0) -> None:
    """Waits for what a datagram or a socket does on its own time, and fails instead of
    hanging when it never happens.
    """
    fim = asyncio.get_running_loop().time() + prazo_s
    while asyncio.get_running_loop().time() < fim:
        if condicao():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the wire never carried what the test waited for")


def test_o_manifesto_e_o_de_uma_tv_que_pareia_e_nao_promete_faixa_seguinte():
    """This TV has no next track at all, and the usual shortcut of mapping it to
    the channel keys would change the channel when a scene asked for the next track.
    """
    manifesto = Samsung.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "tv_samsung"
    assert manifesto.categoria == "tv"
    assert manifesto.nuvem is False
    assert manifesto.auth is Auth.POPUP_NO_APARELHO
    assert manifesto.descoberta.ssdp_fabricantes == ("samsung",)
    assert "proxima" not in manifesto.capacidades
    assert "anterior" not in manifesto.capacidades
    assert {"proxima", "anterior", "sair", "play_pause"}.isdisjoint(manifesto.teclas)
    assert "canal_mais" in manifesto.teclas
    # The token is a secret of the registration, so it never travels back to the panel.
    segredos = [campo.nome for campo in manifesto.config_campos if campo.tipo == "segredo"]
    assert segredos == ["token"]
    sugeridas = por_lista(manifesto)
    assert set(sugeridas) == {"entradas", "atalhos"}
    assert [item.valor for item in sugeridas["entradas"]][:2] == ["KEY_TV", "KEY_HDMI"]


def test_o_nome_do_cliente_viaja_em_base64_sem_caractere_que_seria_codificado():
    """The name is what the TV shows in its popup and keeps in its device list, and it rides
    the query string in base64; a '+', a '/' or a '=' in it would be percent encoded, which
    some firmwares trip over.
    """
    assert samsung.NOME_NA_QUERY == "UUEgSVAgSHVi"
    assert samsung._NA_QUERY.fullmatch(samsung.NOME_NA_QUERY)


async def test_um_poll_le_energia_pela_porta_rest_e_volume_pelo_renderizador(tv):
    async with TvSimulada(volume=42, mudo=True) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.ligado, estado.volume, estado.mudo) == (True, True, 42, True)
    assert estado.detalhe == ""
    # What this protocol never says stays in None, and is not guessed from a sibling.
    assert (estado.fonte, estado.fontes, estado.reproduzindo, estado.tocando) == (
        None,
        (),
        None,
        None,
    )
    assert [acao for acao, _corpo in simulada.soap] == ["GetVolume", "GetMute"]


async def test_o_poll_nunca_abre_o_socket_que_ligaria_a_tv(tv):
    """Opening the WebSocket turns some of these TVs on, so a poll every ten seconds that
    opened one would switch the room on by itself.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        await driver.atualizar()
    assert simulada.aberturas == []
    assert len(simulada.rest) == 2


@pytest.mark.parametrize(
    ("energia", "esperado"),
    [("on", True), ("off", False), ("", None)],
)
async def test_o_ligado_sai_do_powerstate_e_nunca_de_um_socket(tv, energia, esperado):
    """A model that does not publish the field leaves ligado in None: the other way of asking
    is opening the socket, and that is what turns the TV on.
    """
    aparelho = _info()
    if energia:
        aparelho["device"]["PowerState"] = energia
    else:
        del aparelho["device"]["PowerState"]
    async with TvSimulada(info=aparelho) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    assert driver.estado().ligado is esperado
    assert simulada.aberturas == []


async def test_uma_tv_sem_renderizador_nao_tem_volume_e_recusa_o_comando(tv):
    """A model with the UPnP renderer off has no absolute volume at all, and the
    key of volume is a blind step over a state nobody reads back, so the hub says so. The code
    is the one the panel prints as "the device did not answer", because that is the fact: a
    renderer that is not there answered nothing, and erro_aparelho is the panel saying the
    device answered with an error, which would send the integrator hunting the wrong thing.
    """
    async with TvSimulada(renderizador=False) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        estado = driver.estado()
        assert (estado.online, estado.ligado) == (True, True), "a TV without it is still a TV"
        assert (estado.volume, estado.mudo) == (None, None)
        assert await driver.executar("volume", 30) == "eq_offline"
        assert await driver.executar("mudo", True) == "eq_offline"
    assert simulada.teclas() == [], "a blind volume step is not a fallback"


async def test_uma_tv_que_sumiu_responde_o_mesmo_codigo_nos_tres_fios(tv):
    """The same TV pulled from the wall, asked on the three wires it answers on: one fact is
    one stable code. Two codes for it made the panel say the device did not answer for a key
    and that it answered with an error for the volume, and the second one sends the integrator
    looking for a defect in a device that is simply not there.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True
    assert await driver.executar("tecla", "ok") == "eq_offline"
    assert await driver.executar("volume", 30) == "eq_offline"
    assert await driver.executar("mudo", True) == "eq_offline"


async def test_um_renderizador_que_recusa_o_envelope_e_defeito_e_nao_ausencia(tv):
    """The other half of the same rule: a renderer that answered, and answered an error, is
    the device saying no, and that is the code the panel prints as such.
    """
    async with TvSimulada(estado_dmr=500) as simulada:
        driver = tv(simulada)
        assert await driver.executar("volume", 30) == "erro_aparelho"
        assert await driver.executar("mudo", True) == "erro_aparelho"
    assert [acao for acao, _corpo in simulada.soap] == ["SetVolume", "SetMute"]


async def test_o_volume_e_o_mudo_vao_no_envelope_do_renderizador(tv):
    """The renderer already speaks the 0 to 100, so the value goes whole."""
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("volume", 0) is None
        assert await driver.executar("volume", 100) is None
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
    acoes = [acao for acao, _corpo in simulada.soap]
    assert acoes == ["SetVolume", "SetVolume", "SetMute", "SetMute"]
    servico = "urn:schemas-upnp-org:service:RenderingControl:1"
    assert simulada.cabecalhos[0]["SOAPAction"] == f'"{servico}#SetVolume"'
    assert simulada.cabecalhos[0]["Content-Type"] == 'text/xml; charset="utf-8"'
    corpos = [corpo for _acao, corpo in simulada.soap]
    assert "<InstanceID>0</InstanceID><Channel>Master</Channel>" in corpos[0]
    assert "<DesiredVolume>0</DesiredVolume>" in corpos[0]
    assert "<DesiredVolume>100</DesiredVolume>" in corpos[1]
    # The remote key of mute is a toggle, so a mute that is a value and not a flip is
    # the only one the bus can report without lying.
    assert "<DesiredMute>1</DesiredMute>" in corpos[2]
    assert "<DesiredMute>0</DesiredMute>" in corpos[3]
    assert driver.estado().mudo is False


async def test_um_poll_no_ar_nao_apaga_o_volume_que_o_comando_acabou_de_por(tv):
    """The poll does not hold the lock of the commands, on purpose: it can spend the whole
    budget of a poll and a command that waited for it would be cancelled by the gestor. So a
    reading taken before the command must not be published after it: that is not a half built
    state, it is a lost update, and on the bus it is the DP of the level
    reporting the new value, then the old one, then the new one again on the next poll, with
    the reread of 1,5 s confirming the wrong one.
    """
    async with TvSimulada(volume=25, demora_do_mudo=0.4) as simulada:
        driver = tv(simulada)
        poll = asyncio.create_task(driver.atualizar())
        # The poll already read the volume of before the command and is waiting on the mute.
        await _ate(lambda: [acao for acao, _corpo in simulada.soap] == ["GetVolume", "GetMute"])
        assert await driver.executar("volume", 80) is None
        assert driver.estado().volume == 80
        await poll
        assert driver.estado().volume == 80, "the poll published the reading of before the command"
        assert simulada.volume == 80, "and the TV really is at the value of the command"
        # The next poll is a fresh reading, so the guard never freezes the field.
        await driver.atualizar()
    assert driver.estado().volume == 80


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(tv, valor):
    """True is an int in Python: a mute arriving where a volume fits would silence a room."""
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("volume", valor) == "invalid_value"
    assert simulada.soap == []


@pytest.mark.parametrize("valor", ["sim", 1, None, "true"])
async def test_um_mudo_que_nao_e_bandeira_nunca_chega_ao_fio(tv, valor):
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("mudo", valor) == "invalid_value"
    assert simulada.soap == []


async def test_cada_tecla_do_vocabulario_vai_como_a_tecla_desta_tv(tv):
    """The hub speaks its own words and the driver translates each one."""
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        for palavra in ("ok", "voltar", "canal_mais", "digito_7", "guia"):
            assert await driver.executar("tecla", palavra) is None
        assert await driver.executar("tocar") is None
        assert await driver.executar("pausar") is None
        assert await driver.executar("parar") is None
        await _ate(lambda: len(simulada.quadros) == 8)
    assert simulada.teclas() == [
        "KEY_ENTER",
        "KEY_RETURN",
        "KEY_CHUP",
        "KEY_7",
        "KEY_GUIDE",
        "KEY_PLAY",
        "KEY_PAUSE",
        "KEY_STOP",
    ]
    assert set(simulada.comandos()) == {"Click"}
    # The socket confirms nothing, so a command is never read as a fact.
    assert driver.estado().reproduzindo is None


async def test_dois_comandos_seguidos_nao_saem_colados(tv, monkeypatch):
    """This socket breaks under frames sent back to back, and the answer of the
    protocol to that is a single retry, so a scene that fires several steps at the same
    equipment pays a gap between them instead of counting on the retry.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        monkeypatch.setattr(samsung, "INTERVALO_MINIMO_S", 0.2)
        comeco = asyncio.get_running_loop().time()
        assert await driver.executar("tecla", "ok") is None
        assert await driver.executar("tecla", "ok") is None
        gasto = asyncio.get_running_loop().time() - comeco
        await _ate(lambda: len(simulada.quadros) == 2)
    assert gasto >= 0.2


@pytest.mark.parametrize("palavra", ["sair", "play_pause", "proxima", "anterior", "", 7, None])
async def test_uma_tecla_fora_da_tabela_nunca_chega_ao_fio(tv, palavra):
    """A word this TV has no key for is refused here, and not sent to see what happens: the
    TV takes any string and answers nothing, so a wrong key would look like a success.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", palavra) == "invalid_value"
    assert simulada.quadros == []


async def test_a_entrada_e_o_comando_extra_vao_como_a_tecla_escrita(tv):
    """The input of this TV is a key of its reference list, because there is no KEY_HDMI1 to
    KEY_HDMI4: KEY_HDMI walks the inputs and KEY_SOURCE opens the menu.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("fonte", "key_hdmi") is None
        assert await driver.executar("comando_extra", "KEY_CHLIST") is None
        await _ate(lambda: len(simulada.quadros) == 2)
    assert simulada.teclas() == ["KEY_HDMI", "KEY_CHLIST"]
    # Nothing in this protocol reads the active input back, and KEY_HDMI does not even
    # pick one, so publishing the key as the input would be a guess shown as a fact.
    assert driver.estado().fonte is None


@pytest.mark.parametrize(
    "valor",
    ["KEY_FACTORY", "KEY_UP;KEY_DOWN", "hdmi1", "", "KEY_" + "A" * 40, 3, None],
)
async def test_uma_tecla_crua_perigosa_ou_torta_nunca_chega_ao_fio(tv, valor):
    """The service menu key is refused by name: a hub that shipped it inside a shortcut of a
    scene would open the service menu of the TV by remote control.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("comando_extra", valor) == "invalid_value"
    assert simulada.quadros == []


async def test_um_atalho_e_o_lancamento_de_um_aplicativo(tv):
    """The shortcut of this TV is the identifier of an application, which is the only
    deterministic way of landing on something.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("atalho", "111299001912") is None
        await _ate(lambda: len(simulada.quadros) == 1)
    assert simulada.quadros[0] == {
        "method": "ms.channel.emit",
        "params": {
            "event": "ed.apps.launch",
            "to": "host",
            "data": {"action_type": "DEEP_LINK", "appId": "111299001912", "metaTag": ""},
        },
    }


@pytest.mark.parametrize("valor", ["11 22", "a" * 70, "", "app/../outro", 5, None])
async def test_um_atalho_torto_nunca_chega_ao_fio(tv, valor):
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("atalho", valor) == "invalid_value"
    assert simulada.quadros == []


async def test_o_desligar_de_uma_tv_comum_e_um_clique(tv):
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert await driver.executar("desligar") is None
        await _ate(lambda: len(simulada.quadros) == 1)
    assert simulada.teclas() == ["KEY_POWER"]
    assert simulada.comandos() == ["Click"]


async def test_o_desligar_de_uma_frame_segura_a_tecla(tv):
    """A Frame TV reads a click of this key as Art Mode and stays on, so the power off of a
    Frame is press, wait, release; without it the integrator swears the hub does not turn the
    TV off.
    """
    async with TvSimulada(info=_info(FrameTVSupport="true")) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert await driver.executar("desligar") is None
        await _ate(lambda: len(simulada.quadros) == 2)
    assert simulada.teclas() == ["KEY_POWER", "KEY_POWER"]
    assert simulada.comandos() == ["Press", "Release"]


async def test_uma_frame_cancelada_no_meio_ainda_solta_a_tecla(tv, monkeypatch):
    """The gestor gives a call into a driver half the poll interval, and the hold of this key
    is three seconds of it; a call that unwound in the middle would leave the power key held
    down on the TV of a customer.
    """
    async with TvSimulada(info=_info(FrameTVSupport="true")) as simulada:
        driver = tv(simulada)
        # Why after the fixture: it shortens the hold for the whole suite, so a hold set
        # before it never reached this test and the cancel landed wherever it happened to
        # land; the key is only really held down while this sleep is running.
        monkeypatch.setattr(samsung, "SEGUNDOS_SEGURANDO", 1.0)
        monkeypatch.setattr(samsung, "ORCAMENTO_DO_DESLIGAR_S", 6.0)
        await driver.atualizar()
        tarefa = asyncio.create_task(driver.executar("desligar"))
        await _ate(lambda: simulada.comandos() == ["Press"])
        tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)
        await _ate(lambda: simulada.comandos() == ["Press", "Release"])


async def test_o_desligar_de_uma_frame_so_e_segurado_depois_de_um_poll(tv):
    """The Frame is a fact the REST door answers, so a TV that has not been polled yet gets
    the click, which is what every other model takes.
    """
    async with TvSimulada(info=_info(FrameTVSupport="true")) as simulada:
        driver = tv(simulada)
        assert await driver.executar("desligar") is None
        await _ate(lambda: len(simulada.quadros) == 1)
    assert simulada.comandos() == ["Click"]


def test_o_desligar_de_uma_frame_cabe_no_prazo_que_o_gestor_da():
    """The power off of a Frame is an opening of the socket, three seconds of held key and the
    release, all inside ONE call into the driver, and the gestor cancels a call at half the
    poll interval. With the opening budget of any other frame the path spends 5,5 s against a
    ceiling of 5,0 s, and the gestor answers eq_offline for a TV that was turned off: the panel
    and the bus record a failure for a command that worked, and a scene loses the
    step after this one to the deadline.
    """
    teto = gestor.INTERVALO_S * gestor.FRACAO_DO_LIMITE
    assert samsung.ORCAMENTO_DO_DESLIGAR_S < teto
    # The hold is paid out of that budget, and what is left of it is every opening of the path.
    assert samsung.SEGUNDOS_SEGURANDO < samsung.ORCAMENTO_DO_DESLIGAR_S


async def test_a_abertura_do_desligar_de_uma_frame_cabe_no_orcamento_dele(tv, monkeypatch):
    """A TV that accepts the connection and takes its time on the handshake: the opening of
    this path gets what is left of the budget of the power off, and never the budget an
    ordinary frame opens a socket with, which would be paid on top of the held key.
    """
    monkeypatch.setattr(samsung, "PRAZO_DE_ABERTURA_S", 5.0)
    monkeypatch.setattr(samsung, "ORCAMENTO_DO_DESLIGAR_S", 0.4)
    async with TvSimulada(info=_info(FrameTVSupport="true"), demora_do_socket=0.6) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        comeco = asyncio.get_running_loop().time()
        assert await driver.executar("desligar") == "eq_offline"
        gasto = asyncio.get_running_loop().time() - comeco
    assert gasto < 0.5, "the opening took the budget of this call and not the one of a frame"
    assert simulada.quadros == [], "nothing was pressed, so no key was left held down"


async def test_ligar_e_o_pacote_magico_no_endereco_e_na_difusao(tv, monkeypatch):
    """Wake-on-LAN is the only way in with the screen off: the TV answers nothing while it
    sleeps, so the packet goes to the address it had and to the broadcast of the segment.
    """
    async with ServidorDatagrama({}) as segmento, TvSimulada() as simulada:
        monkeypatch.setattr(samsung, "PORTA_WOL", segmento.endereco[1])
        monkeypatch.setattr(samsung, "DIFUSAO", HOST_LOCAL)
        driver = tv(simulada)
        # The MAC is read from the TV itself, which is why the registration field is optional.
        await driver.atualizar()
        assert await driver.executar("ligar") is None
        await _ate(lambda: len(segmento.recebidos) == 2)
    esperado = bytes.fromhex("f" * 12 + MAC * 16)
    assert segmento.recebidos == [esperado, esperado]
    assert len(esperado) == 102
    # The packet leaves whether or not Wake on LAN is enabled on the TV, so the
    # driver never publishes a screen it did not see; the next poll is what says it.
    assert driver.estado().ligado is True


async def test_um_mac_none_da_tv_cai_no_campo_do_cadastro(tv, monkeypatch):
    """This TV answers the string "none" where the MAC of the wifi goes, and reading it as an
    address would put six bytes of nonsense inside the magic packet.
    """
    async with (
        ServidorDatagrama({}) as segmento,
        TvSimulada(info=_info(wifiMac="none")) as simulada,
    ):
        monkeypatch.setattr(samsung, "PORTA_WOL", segmento.endereco[1])
        monkeypatch.setattr(samsung, "DIFUSAO", HOST_LOCAL)
        driver = tv(simulada, mac="00-11-22-33-44-55")
        await driver.atualizar()
        assert await driver.executar("ligar") is None
        await _ate(lambda: len(segmento.recebidos) == 2)
    assert segmento.recebidos[0] == bytes.fromhex("f" * 12 + "001122334455" * 16)


async def test_o_mac_do_cadastro_vence_o_que_a_tv_responde(tv, monkeypatch):
    """The published text of the field says to leave it empty to read the MAC from the TV, so
    a filled field is the explicit override, and it is the only one there is. A TV on cable
    answers the MAC of its wifi card all the same, and the magic packet would go to the
    interface that is not plugged in, on the only way of turning this TV on.
    """
    async with (
        ServidorDatagrama({}) as segmento,
        TvSimulada(info=_info(networkType="wired")) as simulada,
    ):
        monkeypatch.setattr(samsung, "PORTA_WOL", segmento.endereco[1])
        monkeypatch.setattr(samsung, "DIFUSAO", HOST_LOCAL)
        driver = tv(simulada, mac="00-11-22-33-44-55")
        # The poll is what reads the wifiMac of the TV, and it does not win the typed one.
        await driver.atualizar()
        assert await driver.executar("ligar") is None
        await _ate(lambda: len(segmento.recebidos) == 2)
    assert segmento.recebidos[0] == bytes.fromhex("f" * 12 + "001122334455" * 16)
    assert MAC not in segmento.recebidos[0].hex()


async def test_um_pacote_magico_que_nao_saiu_do_hub_nao_e_sucesso(tv, monkeypatch):
    """A container with no permission to broadcast, or a segment where the broadcast address
    is unreachable: the packet never leaves, and None is the word for done, so a
    scene would mark the step as done, the bus would report a success and the TV would stay
    dark. This is the only way of turning this TV on, so the failure is total and silent.
    """

    async def recusar(*_args: object, **_campos: object) -> tuple:
        raise OSError("Network is unreachable")

    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", recusar)
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert await driver.executar("ligar") == "erro_aparelho"


async def test_sem_mac_nenhum_o_ligar_recusa_em_vez_de_atirar_lixo(tv, monkeypatch):
    async with (
        ServidorDatagrama({}) as segmento,
        TvSimulada(info=_info(wifiMac="none")) as simulada,
    ):
        monkeypatch.setattr(samsung, "PORTA_WOL", segmento.endereco[1])
        monkeypatch.setattr(samsung, "DIFUSAO", HOST_LOCAL)
        driver = tv(simulada)
        await driver.atualizar()
        assert await driver.executar("ligar") == "erro_aparelho"
        await asyncio.sleep(0.05)
    assert segmento.recebidos == []


async def test_o_token_do_pareamento_viaja_na_query_e_o_nome_tambem(tv):
    """The TV grants the token as a JSON integer, and a number in the query string of the next
    connection breaks whoever reads it as text.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.autenticar() == "pareado"
        # The socket is kept, so a command right after pairing does not open a second one.
        assert await driver.executar("tecla", "ok") is None
        await driver.parar()
        assert await driver.executar("tecla", "ok") is None
        await _ate(lambda: len(simulada.aberturas) == 2)
    primeira, segunda = simulada.aberturas
    assert f"name={samsung.NOME_NA_QUERY}" in primeira
    assert "token=" not in primeira, "the first connection is the one that earns the token"
    assert f"token={TOKEN}" in segunda


async def test_um_token_do_cadastro_e_usado_antes_de_qualquer_pareamento(tv):
    """The token belongs to the registration, and it is what survives a restart of the daemon."""
    async with TvSimulada() as simulada:
        cadastro = _Cadastro(
            campos={"porta_dmr": str(simulada.porta_dmr)}, segredos={"token": "987654321"}
        )
        driver = tv(simulada, cadastro)
        assert await driver.executar("tecla", "ok") is None
        await _ate(lambda: len(simulada.aberturas) == 1)
    assert "token=987654321" in simulada.aberturas[0]


async def test_um_token_torto_do_cadastro_nao_vai_para_a_query(tv):
    """The token lands in the query string of the socket, so a value with a
    separator in it would write a second parameter nobody wrote in the driver.
    """
    async with TvSimulada() as simulada:
        cadastro = _Cadastro(
            campos={"porta_dmr": str(simulada.porta_dmr)},
            segredos={"token": "1&name=OutroHub"},
        )
        driver = tv(simulada, cadastro)
        assert await driver.executar("tecla", "ok") is None
        await _ate(lambda: len(simulada.aberturas) == 1)
    assert "OutroHub" not in simulada.aberturas[0]
    assert simulada.aberturas[0].count("name=") == 1


async def test_um_token_rotacionado_depois_de_um_quadro_e_recolhido(tv, monkeypatch):
    """The TV rotates the token whenever it feels like it, and it says so right after a frame,
    which is the only place this driver listens; a rotation that was dropped there would put
    the old token in the query of the next connection and stand the popup back up on a TV that
    is already paired.
    """
    monkeypatch.setattr(samsung, "PRAZO_DE_RESPOSTA_S", 1.0)
    rotacao = {"event": "ms.channel.connect", "data": {"token": "555000111"}}
    async with TvSimulada(depois_do_quadro=rotacao) as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "ok") is None
        await driver.parar()
        assert await driver.executar("tecla", "ok") is None
        await _ate(lambda: len(simulada.aberturas) == 2)
    assert f"token={TOKEN}" not in simulada.aberturas[1], "the token of the greeting went stale"
    assert "token=555000111" in simulada.aberturas[1]


async def test_um_token_torto_vindo_da_tv_nao_vai_para_a_query(tv):
    """The token of the TV is the least trusted of the two, because it comes off
    the wire and not from the registration, and it lands in the query string of the next
    connection all the same; a value with a separator in it would write a second parameter
    nobody wrote in the driver.
    """
    torto = {"event": "ms.channel.connect", "data": {"token": "1&name=OutroHub"}}
    async with TvSimulada(abertura=(torto,)) as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "ok") is None
        await driver.parar()
        assert await driver.executar("tecla", "ok") is None
        await _ate(lambda: len(simulada.aberturas) == 2)
    assert "OutroHub" not in simulada.aberturas[1]
    assert simulada.aberturas[1].count("name=") == 1
    assert "token=" not in simulada.aberturas[1]


async def test_um_socket_que_quebrou_na_rajada_e_reaberto_e_a_tecla_chega_uma_vez(tv, monkeypatch):
    """This socket breaks under frames sent back to back, and the answer of this
    protocol to that is a single retry on a socket opened again. What the retry may not do is
    deliver the key twice: a scene that turned the volume up once would turn it up twice.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "ok") is None
        await _ate(lambda: len(simulada.quadros) == 1)
        quebrados: list[str] = []

        async def cano_quebrado(quadro: str) -> None:
            quebrados.append(quadro)
            raise ConnectionResetError("Cannot write to closing transport")

        monkeypatch.setattr(driver._fio, "send_str", cano_quebrado)
        assert await driver.executar("tecla", "voltar") is None
        await _ate(lambda: len(simulada.quadros) == 2)
    assert len(quebrados) == 1, "the frame that broke the pipe is sent again, once"
    assert len(simulada.aberturas) == 2, "on a socket opened again"
    assert simulada.teclas() == ["KEY_ENTER", "KEY_RETURN"], "and the key arrives once"


async def test_o_popup_ainda_na_tela_e_aguardando_e_nao_falhou(tv, monkeypatch):
    """On this side the popup is an open and silent socket, and telling the panel
    it failed would send the integrator to the network instead of to the screen of the TV.
    """
    monkeypatch.setattr(samsung, "PRAZO_DE_PAREAMENTO_S", 0.2)
    async with TvSimulada(abertura=()) as simulada:
        driver = tv(simulada)
        assert await driver.autenticar() == "aguardando"
    assert len(simulada.aberturas) == 1


async def test_uma_tv_que_ninguem_pareou_pede_pareamento_em_vez_de_dizer_offline(tv, monkeypatch):
    """This is the day one of every Samsung install: the poll reads the REST door, which
    answers with no token at all, and publishes the TV as online, while the socket of a command
    opens and stays quiet, which is the popup standing on the screen. Answering eq_offline
    there prints "the device did not answer" under a card that says online, and the integrator
    hunts the network for hours instead of pressing pair once.
    """
    monkeypatch.setattr(samsung, "PRAZO_DE_ABERTURA_S", 0.2)
    monkeypatch.setattr(samsung, "PRAZO_DE_PAREAMENTO_S", 0.2)
    async with TvSimulada(abertura=()) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True, "the REST door answers a TV nobody paired"
        assert await driver.executar("tecla", "ok") == "auth_pendente"
        assert await driver.executar("atalho", "111299001912") == "auth_pendente"
        assert await driver.autenticar() == "aguardando"
    assert simulada.aberturas, "the socket really opened; it is the TV that said nothing on it"
    assert simulada.quadros == [], "and no frame goes out on a socket the TV has not accepted"


async def test_o_ruido_da_abertura_nao_e_lido_como_resposta(tv):
    """The TV emits these two before it says anything useful, and a reader that took the first
    frame as the answer would call a paired TV a failure.
    """
    async with TvSimulada(abertura=(*EVENTOS_DE_RUIDO, EVENTO_CONECTADO)) as simulada:
        driver = tv(simulada)
        assert await driver.autenticar() == "pareado"


async def test_um_pareamento_negado_falha_e_o_comando_pede_auth(tv):
    """The TV says this both when somebody refused the popup and when the token went stale."""
    async with TvSimulada(abertura=(EVENTO_NEGADO,)) as simulada:
        driver = tv(simulada)
        assert await driver.autenticar() == "falhou"
        assert await driver.executar("tecla", "ok") == "auth_pendente"


async def test_uma_tv_de_outra_geracao_e_recusada_com_codigo_claro(tv):
    """An H or J TV answers this: it is not of this generation, and the encrypted port it does
    speak needs a cipher keeps out of this image.
    """
    async with TvSimulada(abertura=(EVENTO_ANTIGO,)) as simulada:
        driver = tv(simulada)
        assert await driver.executar("tecla", "ok") == "nao_suportado"
        assert await driver.autenticar() == "falhou"


async def test_um_socket_que_nao_abre_e_offline_e_nao_defeito(tv):
    async with TvSimulada() as simulada:
        driver = tv(simulada)
    # The TV is gone: the port that answered a moment ago now refuses the connection.
    assert await driver.executar("tecla", "ok") == "eq_offline"


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(tv):
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().online is True
        simulada.estado_rest = 500
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_uma_resposta_sem_os_campos_nao_derruba_o_poll(tv):
    """A body without the fields is a TV that is answering, not a TV that is gone; a model
    that publishes less than another one keeps every fact it does publish.
    """
    magro = {"device": {"PowerState": "on"}, "id": f"uuid:{UUID}"}
    async with TvSimulada(info=magro) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.ligado) == (True, True)
    assert (estado.fonte, estado.tocando, estado.reproduzindo) == (None, None, None)


async def test_uma_resposta_gigante_e_cortada_e_nunca_vira_estado(tv):
    """A device on the LAN never sizes the memory of the hub: a body over the ceiling is cut,
    and what is cut is not JSON any more, so it reads as a TV that did not answer instead of
    as a state. No real TV answers this, and one that does is not answering the device info.
    """
    enorme = {"device": {"PowerState": "on", "lixo": "x" * (samsung.CORPO_MAXIMO * 4)}}
    async with TvSimulada(info=enorme) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        assert driver.estado().ligado is None
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


@pytest.mark.parametrize("corpo", ["", "nao e json", "[1,2,3]", '"texto"'])
async def test_um_corpo_que_nao_e_um_objeto_nao_derruba_o_poll(tv, corpo):
    async with TvSimulada(info=corpo) as simulada:
        driver = tv(simulada)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(tv):
    """Whatever reaches a device takes a literal address and never a name, so the
    hub does not become a proxy into the LAN of the client.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada, _Cadastro(ip="tv-da-sala.local"))
        # One fact, one code: nothing was dialled, so nothing answered, on either wire.
        assert await driver.executar("tecla", "ok") == "eq_offline"
        assert await driver.executar("volume", 20) == "eq_offline"
        assert await driver.autenticar() == "falhou"
        await driver.atualizar()
        await driver.atualizar()
    assert (simulada.rest, simulada.aberturas, simulada.soap) == ([], [], [])
    assert driver.estado().online is False


async def test_a_identidade_e_o_uuid_que_a_tv_responde(tv, monkeypatch):
    """The identity is a uuid and never the address, so a sweep turns a finding
    into a registration nobody has to type.
    """
    async with TvSimulada() as simulada:
        monkeypatch.setattr(samsung, "PORTAS", (simulada.porta,))
        monkeypatch.setattr(samsung, "PORTA_TLS", simulada.porta)
        monkeypatch.setattr(samsung, "ESQUEMA_SEGURO", "http")
        assert await Samsung.identificar(HOST_LOCAL) == UUID
        assert await Samsung.identificar("tv-da-sala.local") is None
    assert simulada.aberturas == [], "asking who it is never opens the socket"


async def test_uma_soundbar_da_mesma_marca_nao_e_esta_tv(tv, monkeypatch):
    """A soundbar of the same maker answers on this same door, and taking it for a TV would
    register a device this driver cannot command.
    """
    soundbar = _info(type="Samsung Soundbar")
    soundbar["type"] = "Samsung Soundbar"
    async with TvSimulada(info=soundbar) as simulada:
        monkeypatch.setattr(samsung, "PORTAS", (simulada.porta,))
        monkeypatch.setattr(samsung, "PORTA_TLS", simulada.porta)
        monkeypatch.setattr(samsung, "ESQUEMA_SEGURO", "http")
        assert await Samsung.identificar(HOST_LOCAL) is None


async def test_a_identidade_vem_sem_o_prefixo_e_o_id_do_topo_e_a_reserva(tv, monkeypatch):
    sem_udn = _info()
    del sem_udn["device"]["udn"]
    async with TvSimulada(info=sem_udn) as simulada:
        monkeypatch.setattr(samsung, "PORTAS", (simulada.porta,))
        monkeypatch.setattr(samsung, "PORTA_TLS", simulada.porta)
        monkeypatch.setattr(samsung, "ESQUEMA_SEGURO", "http")
        assert await Samsung.identificar(HOST_LOCAL) == UUID


@pytest.mark.parametrize("acao", ["agrupar", "temperatura", "modo", "vento", "proxima"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(tv, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        assert await driver.executar(acao, 20) == "nao_suportado"
    assert (simulada.quadros, simulada.soap, simulada.rest) == ([], [], [])


async def test_a_porta_cadastrada_e_tentada_antes_da_outra_e_fica_guardada(tv, monkeypatch):
    """Two ports and nothing in a registration that tells a 2016 model from a 2022 one for
    sure, so the one that answered is kept for the next exchange.
    """
    async with TvSimulada() as simulada:
        driver = tv(simulada)
        # A port nobody listens on comes first, exactly as 8002 does on a TV that only has 8001.
        monkeypatch.setattr(samsung, "PORTAS", (PORTA_MORTA, simulada.porta))
        await driver.atualizar()
        assert driver.estado().online is True
        antes = len(simulada.rest)
        await driver.atualizar()
    assert len(simulada.rest) == antes + 1, "the port that answered is not probed again"


def test_os_codigos_e_os_resultados_do_driver_sao_os_do_contrato():
    """Five stable codes and three results, and nothing else ever leaves a driver."""
    assert {
        samsung.EQ_OFFLINE,
        samsung.INVALID_VALUE,
        samsung.AUTH_PENDENTE,
        samsung.ERRO_APARELHO,
        samsung.NAO_SUPORTADO,
    } <= set(CODIGOS)
    assert {samsung.PAREADO, samsung.AGUARDANDO, samsung.FALHOU} == set(RESULTADOS)
