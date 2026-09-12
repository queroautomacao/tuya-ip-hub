# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Yamaha MusicCast receiver, against a simulated receiver.

A driver is proved against a fake device, never against a board on a bench. What
every test here defends is the one fact that costs the most on this protocol: HTTP 200 is not
success, the response code of the JSON body is, and the volume scale belongs to the model.
"""

import json
from dataclasses import dataclass, field
from typing import Self

import pytest
from aiohttp import web

from iphub.drivers.base import CODIGOS
from iphub.drivers.manifesto import Auth, TipoCampo, por_lista, validar
from iphub.drivers.nativos import yamaha
from iphub.drivers.nativos.yamaha import Yamaha, identidade_da_zona
from iphub.drivers.simulado import ServidorHttp

BASE = "/YamahaExtendedControl/v1/"

APARELHO = BASE + "system/getDeviceInfo"
RECURSOS = BASE + "system/getFeatures"
REDE = BASE + "system/getNetworkStatus"
ESTADO = BASE + "main/getStatus"
ESTADO2 = BASE + "zone2/getStatus"
TOCA = BASE + "netusb/getPlayInfo"

# Three identity fields and none of them guaranteed, plus the MAC of the last resort.
ID = "00A0DEC0FFEE"
SISTEMA = "ABADCAFE"
SERIE = "Z1A234567890BCDE"
MAC = "00A0DE012345"

ZONAS_DE_TESTE = ("main", "zone2")
COMANDOS_DA_ZONA = (
    "setPower",
    "setVolume",
    "setMute",
    "setInput",
    "setSoundProgram",
    "recallScene",
)
COMANDOS_DE_REDE = ("netusb/setPlayback", "netusb/recallPreset")
LEITURAS = (
    "system/getDeviceInfo",
    "system/getFeatures",
    "system/getNetworkStatus",
    "main/getStatus",
    "zone2/getStatus",
    "netusb/getPlayInfo",
)

FUNCOES = ("power", "volume", "mute", "sound_program", "scene")
ENTRADAS = ("hdmi1", "hdmi2", "tv", "net_radio", "tuner", "mc_link", "main_sync")
MODOS = ("straight", "movie", "2ch_stereo")
# Which engine plays each input: only netusb has a transport.
TIPOS = (
    ("hdmi1", "none"),
    ("hdmi2", "none"),
    ("tv", "none"),
    ("net_radio", "netusb"),
    ("tuner", "tuner"),
    ("mc_link", "netusb"),
    ("main_sync", "none"),
)


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = ID
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


def _corpo(**campos: object) -> str:
    """One answer of the API: the response code plus whatever the device chose to send."""
    dados: dict[str, object] = {"response_code": 0}
    dados.update({nome: valor for nome, valor in campos.items() if valor is not None})
    return json.dumps(dados)


def _aparelho(**campos: object) -> str:
    dados: dict[str, object] = {
        "model_name": "RX-V6A",
        "device_id": ID,
        "system_id": SISTEMA,
        "serial_number": SERIE,
        "api_version": 2.0,
        "system_version": 1.61,
    }
    dados.update(campos)
    return _corpo(**dados)


def _zona(
    identificador: str = "main",
    funcoes: tuple[str, ...] = FUNCOES,
    entradas: tuple[str, ...] = ENTRADAS,
    modos: tuple[str, ...] = MODOS,
    faixa: tuple[int, int, int] = (0, 194, 1),
    cenas: int = 4,
) -> dict:
    minimo, maximo, passo = faixa
    return {
        "id": identificador,
        "func_list": list(funcoes),
        "input_list": list(entradas),
        "sound_program_list": list(modos),
        "range_step": [{"id": "volume", "min": minimo, "max": maximo, "step": passo}],
        "scene_num": cenas,
    }


def _recursos(*zonas: dict, tipos: tuple[tuple[str, str], ...] = TIPOS) -> str:
    sistema = {"input_list": [{"id": nome, "play_info_type": tipo} for nome, tipo in tipos]}
    return _corpo(system=sistema, zone=list(zonas or (_zona(), _zona("zone2"))))


def _estado(
    energia: str = "on",
    volume: int = 97,
    mudo: bool = False,
    entrada: str = "hdmi1",
    modo: str = "movie",
    travas: int = 0,
) -> str:
    return _corpo(
        power=energia,
        volume=volume,
        mute=mudo,
        input=entrada,
        sound_program=modo,
        disable_flags=travas,
        # This field is the ceiling the customer set on the receiver and NOT the top of
        # the scale, so it is here to prove the driver reads the scale from getFeatures.
        max_volume=60,
    )


def _toca(reproducao: str = "play", faixa: str = "Radio Paradise") -> str:
    return _corpo(input="net_radio", playback=reproducao, track=faixa, artist="", album="")


def _rede() -> str:
    return _corpo(
        network_name="Sala",
        mac_address={"wired_lan": MAC, "wireless_lan": "", "wireless_direct": ""},
    )


def _com_lixo(documento: str, tamanho: int) -> str:
    """The same answer with padding, to prove a ceiling instead of describing one."""
    dados = json.loads(documento)
    dados["lixo"] = "x" * tamanho
    return json.dumps(dados)


class _ServidorQueDesvia:
    """A receiver answering a redirect to another address, which refuses to follow.

    The simulated server answers a status and a body, and a redirect is a
    header; this is the only device shape the test of that defence needs, so it lives next to
    the test that needs it. Every request is recorded and answered with the same path on the
    host of destino, which is what a hub that followed one would end up asking.
    """

    def __init__(self, destino: str) -> None:
        self.destino = destino
        self.pedidos: list[str] = []
        self.endereco: tuple[str, int] = ("", 0)
        self._runner: web.AppRunner | None = None

    async def __aenter__(self) -> Self:
        app = web.Application()
        app.router.add_route("*", "/{cauda:.*}", self._atender)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sitio = web.TCPSite(self._runner, "127.0.0.1", 0)
        await sitio.start()
        anfitriao, porta = self._runner.addresses[0][:2]
        self.endereco = (anfitriao, porta)
        return self

    async def __aexit__(self, *_erro: object) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    async def _atender(self, request: web.Request) -> web.Response:
        self.pedidos.append(request.path_qs)
        return web.Response(status=302, headers={"Location": self.destino + request.path_qs})


def _rotas(**extras: str) -> dict[str, tuple[int, str]]:
    rotas = {
        APARELHO: (200, _aparelho()),
        RECURSOS: (200, _recursos()),
        REDE: (200, _rede()),
        ESTADO: (200, _estado()),
        ESTADO2: (200, _estado(modo="")),
        TOCA: (200, _toca()),
    }
    for zona in ZONAS_DE_TESTE:
        for comando in COMANDOS_DA_ZONA:
            rotas[f"{BASE}{zona}/{comando}"] = (200, _corpo())
    for comando in COMANDOS_DE_REDE:
        rotas[BASE + comando] = (200, _corpo())
    rotas.update({caminho: (200, texto) for caminho, texto in extras.items()})
    return rotas


@pytest.fixture
async def receiver(monkeypatch):
    """A simulated receiver plus a driver aimed at its port, closed when the test ends."""
    criados: list[Yamaha] = []

    def montar(
        servidor: ServidorHttp | _ServidorQueDesvia | None = None,
        *,
        identidade: str = ID,
        **campos: str,
    ) -> Yamaha:
        if servidor is not None:
            monkeypatch.setattr(yamaha, "PORTA_HTTP", servidor.endereco[1])
        driver = Yamaha(_Cadastro(identidade=identidade, campos=campos))
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


def _caminhos(servidor: ServidorHttp) -> list[str]:
    return [pedido.caminho.removeprefix(BASE) for pedido in servidor.pedidos]


def _comandos(servidor: ServidorHttp) -> list[str]:
    """What the driver WROTE, with the questions of the poll left out."""
    return [caminho for caminho in _caminhos(servidor) if caminho.split("?")[0] not in LEITURAS]


def test_o_manifesto_e_o_de_um_receiver_sem_senha_e_sem_grupo():
    """This API asks for no credential, and agrupar belongs to the multiroom
    category, so a receiver never declares it.
    """
    manifesto = Yamaha.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "receiver_yamaha"
    assert manifesto.categoria == "receiver"
    assert manifesto.auth is Auth.NENHUMA
    assert manifesto.nuvem is False
    assert manifesto.capacidades == (
        "ligar",
        "desligar",
        "volume",
        "mudo",
        "fonte",
        "modo",
        "atalho",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "comando_extra",
    )
    assert "agrupar" not in manifesto.capacidades
    # The reference implements neither controlCursor nor controlMenu, so a tecla
    # here would be code with no reference at all running in a customer house.
    assert "tecla" not in manifesto.capacidades
    assert manifesto.descoberta.ssdp_fabricantes == ("yamaha",)
    assert manifesto.descoberta.mdns_servicos == ()


def test_a_zona_e_campo_do_cadastro_porque_um_avr_tem_duas():
    """The two zones of one AVR answer the same device_id, so the zone separates the
    two registrations and joins the identity.
    """
    campos = Yamaha.MANIFESTO.config_campos
    assert [campo.nome for campo in campos] == ["zona"]
    assert campos[0].padrao == "main"
    assert campos[0].tipo is TipoCampo.TEXTO
    assert identidade_da_zona(ID, "main") == ID
    assert identidade_da_zona(ID, "zone2") == f"{ID}_zone2"


def test_o_driver_sugere_as_palavras_da_tabela_que_ninguem_decora():
    """The driver suggests only what it cannot ask the device for."""
    sugeridas = por_lista(Yamaha.MANIFESTO)
    # This driver asks the receiver for its inputs and its sound programs, so it
    # suggests neither. The ids are of the model: an RX-A1080 answers av1 to av7 and refuses
    # hdmi1, and answers 2ch_stereo and straight and refuses movie and game. A guess here
    # would be a list of buttons that all answer invalid_value, which forbids.
    assert set(sugeridas) == {"atalhos"}
    # A scene is suggested and a preset is not, because a scene of Yamaha already carries
    # the input and the volume with it, and what is suggested here is what a fresh
    # registration spends of the 200 bytes of the profile before the integrator
    # types anything. He adds a preset when his install uses net radio.
    assert [item.valor for item in sugeridas["atalhos"]][:2] == ["scene:1", "scene:2"]
    # The inputs of a group are never suggested: choosing one by hand fakes a group.
    valores = [item.valor for itens in sugeridas.values() for item in itens]
    assert "mc_link" not in valores
    assert "main_sync" not in valores


async def test_um_poll_le_energia_volume_mudo_entrada_e_modo(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    lido = (estado.ligado, estado.mudo, estado.fonte, estado.modo)
    assert lido == (True, False, "hdmi1", "movie")
    # The scale of this model is 0 to 194, so 97 is half of it and never 97 per cent.
    assert estado.volume == 50
    assert estado.detalhe == ""
    # The inputs of a group are filtered out of the list the panel offers.
    assert estado.fontes == ("hdmi1", "hdmi2", "tv", "net_radio", "tuner")


async def test_o_mapa_do_modelo_e_lido_uma_vez_por_sessao(receiver):
    """GetFeatures of an AVR is tens of kilobytes, and asking it every ten seconds on twelve
    equipments is what makes an ARM board and its network suffer.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        for _ in range(3):
            await driver.atualizar()
    caminhos = _caminhos(servidor)
    assert caminhos.count("system/getFeatures") == 1
    assert caminhos.count("system/getDeviceInfo") == 1
    assert caminhos.count("main/getStatus") == 3


async def test_a_identidade_e_perguntada_de_novo_de_tempos_em_tempos(receiver):
    """A lease that moved mid session would leave the hub commanding whoever now
    holds the address, so the small question is asked again about once a minute.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        for _ in range(yamaha.POLLS_ENTRE_IDENTIDADES):
            await driver.atualizar()
        assert _caminhos(servidor).count("system/getDeviceInfo") == 2
        # Another device took the address: the hub stops commanding under this name.
        servidor.rotas[APARELHO] = (200, _aparelho(device_id="00A0DE000000"))
        for _ in range(yamaha.POLLS_ENTRE_IDENTIDADES * 2):
            await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        # The identity is not forgotten when the equipment goes offline, or the stranger
        # holding the address would be adopted under the name of this registration.
        await driver.atualizar()
        assert driver.estado().online is False
        assert await driver.executar("ligar") == "eq_offline"
    assert _comandos(servidor) == []


async def test_um_receiver_sem_campo_de_identidade_e_conferido_pelo_mac(receiver):
    """A receiver old enough to answer none of the three identity fields still has
    a MAC, and comparing nothing at all is what lets the next device_id to show up at this
    address be adopted under the name of this registration.
    """
    so_mac = _aparelho(device_id=None, system_id=None, serial_number=None)
    async with ServidorHttp(_rotas(**{APARELHO: so_mac})) as servidor:
        driver = receiver(servidor, identidade=MAC)
        await driver.atualizar()
        assert driver.estado().online is True
        assert driver.identidade_do_aparelho() == MAC
        # The small question costs one exchange per identity check, never one per poll.
        assert _caminhos(servidor).count("system/getNetworkStatus") == 1
        # Another device, this one with a device_id of its own, took the address.
        servidor.rotas[APARELHO] = (200, _aparelho())
        for _ in range(yamaha.POLLS_ENTRE_IDENTIDADES * 2):
            await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        assert driver.identidade_do_aparelho() == MAC
        assert await driver.executar("ligar") == "eq_offline"
    assert _comandos(servidor) == []


async def test_um_desconhecido_no_endereco_nunca_e_adotado_com_este_nome(receiver):
    """A daemon that starts after the lease moved has nothing of its own to compare
    with, so the identity of the registration is what says the receiver answering here is ours.
    """
    estranho = _aparelho(device_id="00A0DE000000")
    async with ServidorHttp(_rotas(**{APARELHO: estranho})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        assert driver.identidade_do_aparelho() is None
        assert await driver.executar("ligar") == "eq_offline"
    assert _comandos(servidor) == []
    # The map of a receiver that is not ours is never even asked for.
    assert "system/getFeatures" not in _caminhos(servidor)


async def test_o_poll_nunca_pede_o_canal_de_eventos_do_aparelho(receiver):
    """The receiver only starts sending UDP events when a request carries these headers, the
    registration expires in ten minutes and the last process to declare a port wins it; the
    spec says not to send them when no event is wanted, and this hub opens no socket.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    enviados = {chave.lower() for pedido in servidor.pedidos for chave in pedido.cabecalhos}
    assert "x-appname" not in enviados
    assert "x-appport" not in enviados


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [(0, 0), (97, 50), (194, 100), (300, 100), ("x", None), (None, None)],
)
async def test_o_volume_do_modelo_vira_a_escala_da_secao_6(receiver, bruto, esperado):
    """GetStatus answers a max_volume of 60 in every one of these, and using it as the divisor
    is the bug that only shows up in the house that touched that setting.
    """
    lido = _corpo(power="on", volume=bruto, max_volume=60)
    async with ServidorHttp(_rotas(**{ESTADO: lido})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    assert driver.estado().volume == esperado


async def test_o_volume_encaixa_no_passo_do_modelo(receiver):
    """A model that counts in steps of five rounds anything else by itself, and then the
    reread finds a value nobody sent and reports it, once per command.
    """
    recursos = _recursos(_zona(faixa=(0, 60, 5)))
    async with ServidorHttp(_rotas(**{RECURSOS: recursos})) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", 0) is None
        assert await driver.executar("volume", 33) is None
        assert await driver.executar("volume", 50) is None
        assert await driver.executar("volume", 100) is None
    assert _comandos(servidor) == [
        "main/setVolume?volume=0",
        "main/setVolume?volume=20",
        "main/setVolume?volume=30",
        "main/setVolume?volume=60",
    ]


@pytest.mark.parametrize(
    ("faixa", "pedido", "escrito", "publicado"),
    [((0, 60, 5), 10, 5, 8), ((0, 41, 1), 1, 0, 0), ((0, 194, 1), 50, 97, 50)],
)
async def test_o_volume_publicado_e_o_que_a_releitura_vai_achar(
    receiver, faixa, pedido, escrito, publicado
):
    """A command reports optimistically and rereads the device 1,5 s later,
    reporting again only if the device diverged. Publishing the percentage that was ASKED for
    instead of the one the snapped value reads back as makes every command a pair of reports
    in class A, for ever, and the ceiling of 250 a day arrives in half the time.
    """
    async with ServidorHttp(_rotas(**{RECURSOS: _recursos(_zona(faixa=faixa))})) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", pedido) is None
        otimista = driver.estado().volume
        servidor.rotas[ESTADO] = (200, _estado(volume=escrito))
        await driver.atualizar()
    assert _comandos(servidor) == [f"main/setVolume?volume={escrito}"]
    assert (otimista, driver.estado().volume) == (publicado, publicado)


async def test_um_modelo_que_nao_declarou_escala_usa_o_zero_a_cem(receiver):
    """A model that answered no range_step still has to take a volume, and 0 to 100 step 1 is
    the only guess that cannot be worse than refusing the capability outright.
    """
    zona = _zona()
    del zona["range_step"]
    async with ServidorHttp(_rotas(**{RECURSOS: _recursos(zona)})) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", 37) is None
        servidor.rotas[ESTADO] = (200, _estado(volume=37))
        await driver.atualizar()
    assert _comandos(servidor) == ["main/setVolume?volume=37"]
    assert driver.estado().volume == 37


async def test_o_volume_de_um_avr_vai_na_escala_dele(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", 50) is None
        assert await driver.executar("volume", 100) is None
    assert _comandos(servidor) == ["main/setVolume?volume=97", "main/setVolume?volume=194"]


async def test_ligar_desligar_e_o_mudo_falam_a_api(receiver):
    """The spec also takes power=toggle, which this driver never sends: the hub has the two
    halves of the pair and a toggle hides which one the receiver ended up in.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("ligar") is None
        assert await driver.executar("desligar") is None
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
    assert _comandos(servidor) == [
        "main/setPower?power=on",
        "main/setPower?power=standby",
        "main/setMute?enable=true",
        "main/setMute?enable=false",
    ]
    assert (driver.estado().ligado, driver.estado().mudo) == (False, False)


async def test_a_entrada_troca_sem_comecar_a_tocar_sozinha(receiver):
    """SetInput starts playing the new input by default, so a scene that only
    wanted to switch inputs would start a radio.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("fonte", "hdmi2") is None
    assert _comandos(servidor) == ["main/setInput?input=hdmi2&mode=autoplay_disabled"]
    assert driver.estado().fonte == "hdmi2"


async def test_um_receiver_de_api_antiga_recebe_o_modo_vazio(receiver):
    """Autoplay_disabled arrived in API 1.12, and a receiver older than that answers invalid
    parameter to it, so the empty mode the spec accepts is what goes out.
    """
    async with ServidorHttp(_rotas(**{APARELHO: _aparelho(api_version=1.1)})) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("fonte", "tv") is None
    assert _comandos(servidor) == ["main/setInput?input=tv&mode="]


async def test_o_modo_de_som_vai_como_o_programa_da_yamaha(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("modo", "2ch_stereo") is None
    assert _comandos(servidor) == ["main/setSoundProgram?program=2ch_stereo"]
    assert driver.estado().modo == "2ch_stereo"


async def test_um_atalho_e_um_preset_do_receiver_ou_uma_cena_da_zona(receiver):
    """A preset belongs to the single netusb engine and names the zone it plays into; a scene
    belongs to the zone. The two shapes are different on purpose.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("atalho", "preset:3") is None
        assert await driver.executar("atalho", "scene:2") is None
    assert _comandos(servidor) == [
        "netusb/recallPreset?zone=main&num=3",
        "main/recallScene?num=2",
    ]


@pytest.mark.parametrize(
    "valor",
    ["preset:0", "preset:41", "scene:0", "scene:9", "preset:x", "preset:", "radio", "", 7, None],
)
async def test_um_atalho_fora_da_gramatica_nunca_chega_ao_fio(receiver, valor):
    """The model of these tests answers four scenes, so scene:9 is a shortcut that would only
    ever fail on the wire.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("atalho", valor) == "invalid_value"
    assert _comandos(servidor) == []


async def test_o_transporte_so_existe_na_entrada_de_rede(receiver):
    """The transport is the single netusb engine of the receiver, not something the
    zone owns, and on HDMI there is no transport at all to refuse or accept.
    """
    async with ServidorHttp(_rotas(**{ESTADO: _estado(entrada="net_radio")})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        for acao in ("tocar", "pausar", "parar", "proxima", "anterior"):
            assert await driver.executar(acao) is None
    assert _comandos(servidor) == [
        "netusb/setPlayback?playback=play",
        "netusb/setPlayback?playback=pause",
        "netusb/setPlayback?playback=stop",
        "netusb/setPlayback?playback=next",
        "netusb/setPlayback?playback=previous",
    ]


@pytest.mark.parametrize("acao", ["tocar", "pausar", "parar", "proxima", "anterior"])
async def test_em_hdmi_nao_ha_transporte_para_comandar(receiver, acao):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert await driver.executar(acao) == "nao_suportado"
    assert _comandos(servidor) == []


async def test_reproduzindo_e_tocando_sao_fatos_diferentes(receiver):
    """Decision of 3/set/2026: one is the transport and the other is the title. The
    reference answers PLAYING for any zone that is on and not netusb, which reports an HDMI
    that nobody is listening to as playing.
    """
    de_rede = {ESTADO: _estado(entrada="net_radio")}
    async with ServidorHttp(_rotas(**de_rede)) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert (driver.estado().reproduzindo, driver.estado().tocando) == (True, "Radio Paradise")
        # The spec warns that text stays in track with the playback stopped.
        servidor.rotas[TOCA] = (200, _toca(reproducao="stop"))
        await driver.atualizar()
    assert (driver.estado().reproduzindo, driver.estado().tocando) == (False, None)


@pytest.mark.parametrize(
    ("reproducao", "reproduzindo", "titulo"),
    [
        ("play", True, "Radio Paradise"),
        ("pause", False, None),
        ("stop", False, None),
        ("fast_forward", True, "Radio Paradise"),
        ("fast_reverse", True, "Radio Paradise"),
    ],
)
async def test_os_cinco_estados_de_reproducao_do_motor(receiver, reproducao, reproduzindo, titulo):
    """Playback has five words in the spec and three of them are an engine that is running:
    reading a fast forward as stopped is the decision of 3/set/2026 broken, and
    the app answers it by sending play to what already plays.
    """
    rotas = {ESTADO: _estado(entrada="net_radio"), TOCA: _toca(reproducao=reproducao)}
    async with ServidorHttp(_rotas(**rotas)) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    assert (driver.estado().reproduzindo, driver.estado().tocando) == (reproduzindo, titulo)


async def test_parar_solta_o_titulo_junto_com_o_transporte(receiver):
    """The transport and the title are different facts, and they are both false
    after a stop; leaving the old title pinned reports a silent receiver playing a radio for
    up to ten seconds, until the next poll says otherwise.
    """
    async with ServidorHttp(_rotas(**{ESTADO: _estado(entrada="net_radio")})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert driver.estado().tocando == "Radio Paradise"
        for acao in ("parar", "pausar"):
            assert await driver.executar(acao) is None
            assert (driver.estado().reproduzindo, driver.estado().tocando) == (False, None)
        assert await driver.executar("tocar") is None
        assert driver.estado().reproduzindo is True


async def test_fora_da_rede_o_transporte_nao_e_pausado_e_sim_inexistente(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    assert driver.estado().reproduzindo is None
    assert driver.estado().tocando is None
    # The engine is not even asked while the input of the zone is an HDMI.
    assert "netusb/getPlayInfo" not in _caminhos(servidor)


async def test_o_motor_que_nao_respondeu_nao_derruba_o_poll(receiver):
    """The zone already answered, so the receiver is here; only the title and the transport are
    missing, and losing those two is not losing the equipment.
    """
    rotas = {ESTADO: _estado(entrada="net_radio"), TOCA: (json.dumps({"response_code": 2}))}
    async with ServidorHttp(_rotas(**rotas)) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().reproduzindo is None


async def test_uma_capacidade_que_a_zona_nao_tem_e_recusada_sem_ir_ao_fio(receiver):
    """Func_list of the zone is the list of truth, and a second zone usually has no sound
    program; a driver that assumed main has everything sends a command that is guarded in
    silence.
    """
    recursos = _recursos(_zona("zone2", funcoes=("power", "mute"), modos=()))
    async with ServidorHttp(_rotas(**{RECURSOS: recursos})) as servidor:
        driver = receiver(servidor, zona="zone2")
        await driver.atualizar()
        assert await driver.executar("modo", "movie") == "nao_suportado"
        assert await driver.executar("volume", 30) == "nao_suportado"
        assert await driver.executar("atalho", "scene:1") == "nao_suportado"
        assert await driver.executar("ligar") is None
    assert _comandos(servidor) == ["zone2/setPower?power=on"]
    estado = driver.estado()
    # The field a zone does not carry is None, and never a zero that looks like an answer.
    assert (estado.volume, estado.modo) == (None, None)
    assert estado.ligado is True


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(receiver, valor):
    """True is an int in Python: a mute arriving where a volume fits would put the receiver one
    step above its floor instead of silencing it.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", valor) == "invalid_value"
    assert _comandos(servidor) == []


@pytest.mark.parametrize(
    "valor",
    ["hdmi1&power=standby", "hdmi1?x=1", "", "mc_link", "main_sync", "hdmi9", 7, None, "hdmi 1"],
)
async def test_uma_entrada_fora_do_contrato_nunca_chega_ao_fio(receiver, valor):
    """The value lands in the query string, so a separator in it would write a
    second parameter nobody wrote in that file; and mc_link is the input of a group, which
    chosen by hand fakes a group that was never built.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("modo", valor) == "invalid_value"
    assert _comandos(servidor) == []


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [(3, "nao_suportado"), (4, "invalid_value"), (5, "erro_aparelho"), (1, "erro_aparelho")],
)
async def test_http_200_nao_e_sucesso_o_codigo_do_corpo_e(receiver, codigo, esperado):
    """The spec is explicit: no other data is included in a response whose response code was
    other than zero. A driver that only read the status line would report every refusal as done.
    """
    recusa = {f"{BASE}main/setPower": json.dumps({"response_code": codigo})}
    async with ServidorHttp(_rotas(**recusa)) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("ligar") == esperado
    assert _comandos(servidor) == ["main/setPower?power=on"]


async def test_um_poll_que_o_aparelho_recusou_e_um_poll_perdido(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.rotas[ESTADO] = (200, json.dumps({"response_code": 5}))
        await driver.atualizar()
        assert driver.estado().online is True, "um poll perdido guarda o último estado"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_um_receiver_que_nao_responde_e_offline_depois_de_dois_polls(receiver):
    """One lost poll keeps the last state, two in a row is offline, and a receiver in standby
    with Network Standby off is exactly this case.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is True, "um poll perdido guarda o último estado"
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    assert driver.estado().tocando is None


async def test_um_receiver_que_voltou_tem_o_mapa_dele_lido_de_novo(receiver):
    """What answers at this address after a silence may be another device, or the same one
    after an update that changed its map.
    """
    async with ServidorHttp(_rotas()) as servidor:
        porta = servidor.endereco[1]
        driver = receiver(servidor)
        await driver.atualizar()
        servidor.rotas.clear()
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        servidor.rotas.update(_rotas())
        await driver.atualizar()
    assert porta == servidor.endereco[1]
    assert driver.estado().online is True
    assert _caminhos(servidor).count("system/getFeatures") == 2


async def test_uma_resposta_sem_os_campos_nao_derruba_o_poll(receiver):
    async with ServidorHttp(_rotas(**{ESTADO: json.dumps({"response_code": 0})})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.volume, estado.mudo, estado.fonte, estado.modo) == (
        None,
        None,
        None,
        None,
        None,
    )


async def test_uma_resposta_gigante_e_um_poll_perdido_e_nao_uma_queda(receiver):
    """The answer of a device on the LAN of the customer never sizes the memory of the hub, so
    a body past the ceiling comes out cut, stops being JSON and is a poll that failed.
    """
    enorme = json.dumps({"response_code": 0, "power": "on", "lixo": "x" * 200_000})
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        servidor.rotas[ESTADO] = (200, enorme)
        await driver.atualizar()
        assert driver.estado().online is True, "um poll perdido guarda o último estado"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


@pytest.mark.parametrize("acao", ["tecla", "temperatura", "vento", "agrupar"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(receiver, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar(acao, 21) == "nao_suportado"
    assert servidor.pedidos == []


@pytest.mark.parametrize(
    ("campos", "esperado"),
    [
        ({}, ID),
        ({"device_id": None}, SISTEMA),
        ({"device_id": None, "system_id": None}, SERIE),
        ({"device_id": None, "system_id": None, "serial_number": None}, MAC),
        ({"device_id": "com espaco"}, SISTEMA),
    ],
)
async def test_a_identidade_e_o_que_o_aparelho_diz_que_e(receiver, monkeypatch, campos, esperado):
    """Three identity fields and none of them guaranteed. The order is fixed here so
    the same receiver never becomes two registrations depending on where the hub came in from.
    """
    async with ServidorHttp(_rotas(**{APARELHO: _aparelho(**campos)})) as servidor:
        monkeypatch.setattr(yamaha, "PORTA_HTTP", servidor.endereco[1])
        assert await Yamaha.identificar("127.0.0.1") == esperado


async def test_a_identidade_nunca_e_perguntada_a_um_nome(receiver, monkeypatch):
    """Only an IP literal reaches a device, so the hub is never a resolver for the
    LAN of the customer.
    """
    async with ServidorHttp(_rotas()) as servidor:
        monkeypatch.setattr(yamaha, "PORTA_HTTP", servidor.endereco[1])
        assert await Yamaha.identificar("receiver.local") is None
        assert await Yamaha.identificar("") is None
    assert servidor.pedidos == []


async def test_um_aparelho_que_recusou_a_pergunta_nao_tem_identidade(receiver, monkeypatch):
    recusa = {APARELHO: json.dumps({"response_code": 2}), REDE: json.dumps({"response_code": 2})}
    async with ServidorHttp(_rotas(**recusa)) as servidor:
        monkeypatch.setattr(yamaha, "PORTA_HTTP", servidor.endereco[1])
        assert await Yamaha.identificar("127.0.0.1") is None


async def test_a_zona_do_cadastro_e_a_que_fala_e_a_que_identifica(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor, zona="zone2")
        await driver.atualizar()
        assert await driver.executar("ligar") is None
    assert "zone2/getStatus" in _caminhos(servidor)
    assert "main/getStatus" not in _caminhos(servidor)
    assert _comandos(servidor) == ["zone2/setPower?power=on"]
    assert driver.identidade_do_aparelho() == f"{ID}_zone2"


async def test_uma_zona_que_o_receiver_nao_tem_e_um_cadastro_impossivel(receiver):
    """A registration that names zone3 on a two zone AVR can never be commanded, and saying so
    by code is what gets the field corrected.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor, zona="zone3")
        assert await driver.executar("ligar") == "invalid_value"
        for _ in range(5):
            await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "invalid_value"
    assert _comandos(servidor) == []
    # The zone of the registration is a question asked OF the map, not a reason to
    # download it again; a receiver that has no zone3 answered the same document every ten
    # seconds, tens of kilobytes at a time, until somebody corrected the field.
    caminhos = _caminhos(servidor)
    assert caminhos.count("system/getFeatures") == 1
    assert caminhos.count("system/getDeviceInfo") == 1


@pytest.mark.parametrize("zona", ["cozinha", "main2", "zone5", "zone2 main", "main;zone2"])
async def test_uma_zona_fora_do_vocabulario_nunca_fala_com_ninguem(receiver, zona):
    """A typo in this field must not become traffic on the LAN of the customer, and a
    zone is half of a path, so a word with a separator in it would write a request of its own.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor, zona=zona)
        assert await driver.executar("ligar") == "invalid_value"
        await driver.atualizar()
    assert servidor.pedidos == []
    assert driver.estado().online is False


@pytest.mark.parametrize("zona", ["", "MAIN", " Main ", None])
async def test_um_cadastro_sem_zona_e_o_aparelho_de_uma_zona_so(receiver, zona):
    """The default of the field is main, and an integrator who typed it in capitals meant the
    same zone; a receiver of one zone never has this field filled in at all.
    """
    campos = {} if zona is None else {"zona": zona}
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor, **campos)
        await driver.atualizar()
    assert driver.estado().online is True
    assert "main/getStatus" in _caminhos(servidor)


async def test_o_receiver_guardando_o_volume_recusa_pelo_aparelho(receiver):
    """Disable_flags says what is inoperative right now (bit 0 volume, bit 1 mute): with the
    bit on the receiver answers guarded and nothing happens, and reading the field is what
    tells a refusal of the device from a mistake of the hub.
    """
    async with ServidorHttp(_rotas(**{ESTADO: _estado(travas=0b11)})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert await driver.executar("volume", 40) == "erro_aparelho"
        assert await driver.executar("mudo", True) == "erro_aparelho"
        # A value the hub itself would refuse is refused as such, guard or no guard.
        assert await driver.executar("volume", 200) == "invalid_value"
        assert await driver.executar("ligar") is None
    assert _comandos(servidor) == ["main/setPower?power=on"]


async def test_o_comando_extra_fica_dentro_deste_receiver(receiver):
    """The address is always this receiver behind the fixed base of the API, so an extra
    command is one call of it and never a door out into the LAN.
    """
    rotas = {f"{BASE}main/setSleep": _corpo(), f"{BASE}main/setBass": _corpo()}
    async with ServidorHttp(_rotas(**rotas)) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("comando_extra", "main/setSleep?sleep=30") is None
        assert await driver.executar("comando_extra", " main/setBass?val=2 ") is None
    assert _comandos(servidor) == ["main/setSleep?sleep=30", "main/setBass?val=2"]


@pytest.mark.parametrize(
    "valor",
    [
        "http://outro/x",
        "//outro/x",
        "../system/getDeviceInfo",
        "main/setSleep?ip=1.2.3.4:80",
        "user@host/x",
        "MAIN/setPower?power=on",
        "main/setSleep?sleep=30#x",
        "main/set sleep",
        "system/getDeviceInfo/../..",
        "",
        7,
        None,
    ],
)
async def test_um_comando_extra_que_sairia_deste_receiver_e_recusado(receiver, valor):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("comando_extra", valor) == "invalid_value"
    assert _comandos(servidor) == []


async def test_um_receiver_que_desvia_nunca_leva_o_hub_a_outro_host(receiver):
    """A receiver answering a redirect would send the hub to whatever host it
    names, and following it is exactly the LAN proxy the hub refuses to be. The answer is not
    a document either, so the exchange is a failure and not a silent success.
    """
    async with ServidorHttp(_rotas()) as destino:
        alvo = f"http://127.0.0.1:{destino.endereco[1]}"
        async with _ServidorQueDesvia(alvo) as desvio:
            driver = receiver(desvio)
            assert await driver.executar("ligar") == "erro_aparelho"
            await driver.atualizar()
            assert driver.estado().online is False
            # The sweep asks with no registration at all and follows nothing either.
            assert await Yamaha.identificar("127.0.0.1") is None
    assert desvio.pedidos != []
    assert destino.pedidos == []


async def test_o_mapa_do_modelo_tem_um_teto_proprio_e_maior(receiver):
    """GetFeatures is the map of the model, tens of kilobytes on an AVR, so it carries a
    ceiling of its own instead of forcing every other answer to have that room; past that
    ceiling the body comes out cut, stops being JSON and is a poll that failed.
    """
    grande = _com_lixo(_recursos(), 100_000)
    assert len(grande) > yamaha.CORPO_MAXIMO
    async with ServidorHttp(_rotas(**{RECURSOS: grande})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        assert driver.estado().fontes == ("hdmi1", "hdmi2", "tv", "net_radio", "tuner")
    enorme = _com_lixo(_recursos(), yamaha.CORPO_DE_RECURSOS)
    async with ServidorHttp(_rotas(**{RECURSOS: enorme})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(receiver, monkeypatch):
    """The address is where the device answered today, and a registration without
    one is a registration nothing can be dialled from.
    """
    async with ServidorHttp(_rotas()) as servidor:
        monkeypatch.setattr(yamaha, "PORTA_HTTP", servidor.endereco[1])
        driver = Yamaha(_Cadastro(ip="receiver.local"))
        assert await driver.executar("ligar") == "eq_offline"
        await driver.atualizar()
        await driver.parar()
    assert servidor.pedidos == []
    assert driver.estado().online is False


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """Five stable codes and nothing else ever leaves a driver."""
    assert {
        yamaha.EQ_OFFLINE,
        yamaha.INVALID_VALUE,
        yamaha.ERRO_APARELHO,
        yamaha.NAO_SUPORTADO,
    } <= set(CODIGOS)
