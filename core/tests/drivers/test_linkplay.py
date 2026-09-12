# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The multiroom driver against a simulated speaker, no hardware, ever.

Every fact that cost days on the bench is a test that ATTACKS it here: a play
on a slave must never reach the wire, a slave answering stop must read as playing, a title
of the previous source must never leak into a line input, and the minimum between two frames
of the control port must be honoured even when nobody is watching the clock.

The wire vocabulary is written by hand in this file. A test that imported the commands from
the driver would agree with any change the driver made to them, which is exactly what a
protocol test exists to catch.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from iphub.config import Item
from iphub.drivers import catalogo
from iphub.drivers.base import CODIGOS
from iphub.drivers.descoberta import montar
from iphub.drivers.manifesto import Auth, item_valido, por_lista, validar
from iphub.drivers.nativos import linkplay
from iphub.drivers.nativos.linkplay import ENTRADA_DE_REDE, Escravo, LinkPlay
from iphub.drivers.simulado import ServidorHttp

CAMINHO = "/httpapi.asp?command="
PEDE_IDENTIDADE = "getStatusEx"
PEDE_ESTADO = "getPlayerStatus"
PEDE_ESCRAVOS = "multiroom:getSlaveList"
DESFAZ_GRUPO = "multiroom:Ungroup"

IDENTIDADE = "FF31F09E1A5020554E1CD9F1"
OUTRA_IDENTIDADE = "AA11BB22CC33DD44EE55FF66"
IP_DO_MESTRE = "10.0.0.9"
IP_DO_ESCRAVO = "10.0.0.11"

# The firmware answers metadata in hexadecimal, so the test writes the hexadecimal by hand:
# computing it here with the same call the driver makes would agree with itself.
TITULO_EM_HEX = "4d75736963612031"
ARTISTA_EM_HEX = "41727469737461"
ANTIGO_EM_HEX = "526164696f20416e74696761"
TITULO = "Musica 1"
ARTISTA = "Artista"

# The mask of a box that has a line input and bluetooth, and no usb and no optical.
MASCARA = "0x6"
MODO_DE_REDE = "10"
MODO_DE_LINHA = "40"
MODO_ESCRAVO = "99"

URL = "http://10.0.0.2/audio/bipe.wav"


@dataclass(frozen=True)
class _Cadastro:
    """A registration whose identity is NOT the uuid of the speaker, on purpose: an
    integrator names a block what he likes, and the identity of the box is what the box says.
    """

    identidade: str = "cozinha"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    # The lists of the registration, where a shortcut carries the label the
    # integrator gave it and the value the driver puts on the wire.
    listas: dict[str, tuple] = field(default_factory=dict)


def _identidade(**extra: str) -> str:
    return json.dumps({"uuid": IDENTIDADE, "plm_support": MASCARA, **extra})


def _tocador(**extra: str) -> str:
    lido = {
        "mode": MODO_DE_REDE,
        "status": "play",
        "vol": "50",
        "mute": "0",
        "Title": TITULO_EM_HEX,
        "Artist": ARTISTA_EM_HEX,
    }
    return json.dumps({**lido, **extra})


def _rotas(respostas: dict[str, str]) -> dict[str, tuple[int, str]]:
    return {CAMINHO + comando: (200, corpo) for comando, corpo in respostas.items()}


def _fala(estado: str = "", identidade: str = "", **outros: str) -> dict[str, tuple[int, str]]:
    """The two questions of a poll answered, plus whatever the test wants on top."""
    respostas = {
        PEDE_IDENTIDADE: identidade or _identidade(),
        PEDE_ESTADO: estado or _tocador(),
    }
    return _rotas({**respostas, **outros})


async def _ate(condicao: Callable[[], bool], prazo_s: float = 2.0) -> None:
    """Waits for the simulated speaker to have handled what the driver sent.

    A frame of the control port is written and the socket is closed, and the speaker
    reads it in a task of its own, so asserting the instant the driver returned is a test
    that passes on a quiet machine and fails on a busy one.
    """
    laco = asyncio.get_running_loop()
    limite = laco.time() + prazo_s
    while not condicao():
        assert laco.time() < limite, "the simulated speaker never saw it"
        await asyncio.sleep(0.005)


def _comandos(aparelho: ServidorHttp) -> list[str]:
    return [p.caminho.removeprefix(CAMINHO) for p in aparelho.pedidos]


@pytest.fixture
async def caixa(monkeypatch):
    """Builds a driver aimed at the simulated servers and closes it when the test ends."""
    criados: list[LinkPlay] = []

    def montar_driver(
        aparelho: ServidorHttp | None = None, *, ip: str = "127.0.0.1", listas: dict | None = None
    ) -> LinkPlay:
        if aparelho is not None:
            monkeypatch.setattr(linkplay, "PORTA_HTTP", aparelho.endereco[1])
        driver = LinkPlay(_Cadastro(ip=ip, listas=listas or {}))
        criados.append(driver)
        return driver

    yield montar_driver
    for driver in criados:
        await driver.parar()


def test_o_manifesto_e_valido_e_nao_promete_ligar_nem_desligar():
    """Sections 6 and 14: the speaker is always on, so the capability is OMITTED, never
    implemented to refuse.
    """
    manifesto = LinkPlay.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "multiroom_linkplay"
    assert manifesto.categoria == "multiroom"
    assert manifesto.auth is Auth.NENHUMA
    assert manifesto.capacidades == (
        "volume",
        "mudo",
        "fonte",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "agrupar",
        "atalho",
    )
    assert "ligar" not in manifesto.capacidades
    assert "desligar" not in manifesto.capacidades
    # The ip is the address the discovery re-resolves, and this protocol fixes
    # both ports, so the registration asks for nothing else.
    assert manifesto.config_campos == ()


def test_os_textos_do_manifesto_estao_nos_dois_idiomas():
    """Every message the panel shows about the driver comes from textos."""
    textos = LinkPlay.MANIFESTO.textos
    assert set(textos) == {"pt", "en"}
    assert set(textos["pt"]) == set(textos["en"])
    assert "descricao" in textos["pt"]
    for capacidade in ("fonte", "tocar", "agrupar", "atalho"):
        assert f"cap_{capacidade}" in textos["pt"]


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """A code invented here is a phrase the panel cannot translate."""
    usados = {
        linkplay.EQ_OFFLINE,
        linkplay.INVALID_VALUE,
        linkplay.ERRO_APARELHO,
        linkplay.RECUSA_DE_GRUPO,
    }
    assert usados <= set(CODIGOS)


def test_o_catalogo_encontra_a_caixa_sem_lista_na_mao():
    """CATALOGO is walked, and nobody edits a list by hand."""
    catalogo.esquecer()
    try:
        assert catalogo.carregar()["multiroom_linkplay"] is LinkPlay
    finally:
        catalogo.esquecer()


def test_a_descoberta_gerada_reivindica_o_servico_da_caixa():
    """The sweep plan is GENERATED from the manifest, never written beside it."""
    assert montar([LinkPlay.MANIFESTO]).mdns == {"_linkplay._tcp": "multiroom_linkplay"}


async def test_a_identidade_vem_do_uuid_e_nunca_do_ip(caixa):
    """The identity is the uuid, the ip is only where it answered today."""
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert driver.identidade_do_aparelho() is None
        await driver.atualizar()
    assert driver.identidade_do_aparelho() == IDENTIDADE
    assert driver.identidade_do_aparelho() != driver.cadastro.identidade
    assert driver.identidade_do_aparelho() != driver.cadastro.ip


async def test_a_caixa_que_voltou_e_perguntada_de_novo_quem_ela_e(caixa):
    """A speaker comes back by its identity in about 50 s, and its address may
    have moved on to another box while it was away.
    """
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.identidade_do_aparelho() == IDENTIDADE
        aparelho.rotas.clear()
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        aparelho.rotas.update(_fala(identidade=_identidade(uuid=OUTRA_IDENTIDADE)))
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.identidade_do_aparelho() == OUTRA_IDENTIDADE


async def test_dois_polls_falhos_deixam_offline_e_um_certo_traz_de_volta(caixa):
    """One lost poll is not a speaker that went away, two in a row is."""
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.estado().online is True
        aparelho.rotas.clear()
        await driver.atualizar()
        assert driver.estado().online is True, "one failure must not blink the panel offline"
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "erro_aparelho"
        aparelho.rotas.update(_fala())
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().detalhe == ""


async def test_o_escravo_que_responde_stop_toca_o_que_o_mestre_toca(caixa):
    """A slave answers stop even while the group plays."""
    parado = _tocador(mode=MODO_ESCRAVO, status="stop")
    async with ServidorHttp(_fala(estado=parado)) as aparelho:
        driver = caixa(aparelho)
        driver.espelhar("Faixa do mestre")
        await driver.atualizar()
    assert driver.e_escravo() is True
    assert driver.estado().tocando == "Faixa do mestre"


async def test_o_escravo_que_saiu_do_modo_multiroom_por_dois_polls_e_reportado(caixa):
    """The physical group dissolved by itself and the logical state has to be
    reconciled; one poll out of the mode is not enough to say so.
    """
    async with ServidorHttp(_fala(estado=_tocador(mode=MODO_ESCRAVO))) as aparelho:
        driver = caixa(aparelho)
        driver.espelhar("Faixa do mestre")
        await driver.atualizar()
        assert driver.e_escravo() is True
        aparelho.rotas.update(_fala())
        await driver.atualizar()
        assert driver.saiu_do_grupo() is False, "one poll out of the mode is a hiccup"
        assert driver.e_escravo() is True
        await driver.atualizar()
    assert driver.saiu_do_grupo() is True
    assert driver.e_escravo() is False
    assert driver.estado().tocando == f"{TITULO} - {ARTISTA}"


async def test_so_as_entradas_que_o_plm_support_declara_sao_oferecidas(caixa):
    """The mask says which inputs the hardware really has, and an input outside
    it is a button on the panel that only ever fails.
    """
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.estado().fontes == ("wifi", "line-in", "bluetooth")
        antes = len(aparelho.pedidos)
        assert await driver.executar("fonte", "optical") == "invalid_value"
        assert await driver.executar("fonte", "usb") == "invalid_value"
        assert len(aparelho.pedidos) == antes, "a refused input must never reach the wire"


@pytest.mark.parametrize("marcado", [False, True])
async def test_entrada_com_grupo_ativo_e_recusada_antes_do_fio(caixa, marcado):
    """Setting an input while a group is active breaks the group, whether the
    speaker itself says it is a slave or the owner of the group says it belongs to one.
    """
    modo = MODO_DE_REDE if marcado else MODO_ESCRAVO
    async with ServidorHttp(_fala(estado=_tocador(mode=modo))) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        if marcado:
            driver.marcar_grupo(True)
        antes = len(aparelho.pedidos)
        assert await driver.executar("fonte", "line-in") == "nao_suportado"
        assert await driver.executar("fonte", "wifi") == "nao_suportado"
        assert len(aparelho.pedidos) == antes


async def test_o_transporte_de_um_escravo_nunca_chega_ao_fio(caixa):
    """A play on a slave dismantles the group, so the transport of a group goes
    to the master and this driver refuses it here instead of losing the group.
    """
    async with ServidorHttp(_fala(estado=_tocador(mode=MODO_ESCRAVO))) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        antes = len(aparelho.pedidos)
        for acao, valor in (
            ("tocar", URL),
            ("pausar", None),
            ("proxima", None),
            ("anterior", None),
            ("volume", 30),
            ("atalho", "preset:1"),
            ("atalho", URL),
        ):
            assert await driver.executar(acao, valor) == "nao_suportado", acao
        assert len(aparelho.pedidos) == antes


async def test_o_titulo_da_fonte_anterior_nao_vaza_para_a_entrada_de_linha(caixa):
    """The firmware does not clear Title and Artist when the source changes, so
    a line input playing would show the last track of the radio.
    """
    de_linha = _tocador(mode=MODO_DE_LINHA, Title=ANTIGO_EM_HEX, Artist="")
    async with ServidorHttp(_fala(estado=de_linha)) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
    assert driver.estado().fonte == "line-in"
    assert driver.estado().tocando is None


@pytest.mark.parametrize("situacao", ["pause", "stop", "load"])
async def test_o_que_nao_esta_tocando_nao_reporta_titulo(caixa, situacao):
    """Estado.tocando carries the title while the transport plays and nothing while it does
    not, which is what the play DP is read from.
    """
    async with ServidorHttp(_fala(estado=_tocador(status=situacao))) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
    assert driver.estado().tocando is None


async def test_o_volume_vai_e_volta_no_0_a_100(caixa):
    """The volume is ALWAYS 0 to 100, in both directions."""
    rotas = _fala(estado=_tocador(vol="77"))
    rotas.update(_rotas({"setPlayerCmd:vol:50": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("volume", 50) is None
        assert driver.estado().volume == 50
        assert _comandos(aparelho) == ["setPlayerCmd:vol:50"]
        await driver.atualizar()
    assert driver.estado().volume == 77


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [("0", 0), ("100", 100), ("255", 100), ("-5", 0), ("abc", None), ("", None)],
)
async def test_o_volume_que_a_caixa_responde_e_preso_a_faixa_do_contrato(caixa, bruto, esperado):
    """A speaker on the LAN answers what it likes, and the panel still reads a 0 to 100."""
    async with ServidorHttp(_fala(estado=_tocador(vol=bruto))) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
    assert driver.estado().volume == esperado


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_volume_fora_do_contrato_nunca_chega_ao_fio(caixa, valor):
    """True is an int in Python: a mute arriving where a volume belongs would silence a box."""
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("volume", valor) == "invalid_value"
        assert aparelho.pedidos == []


@pytest.mark.parametrize(
    "valor",
    [
        "http://10.0.0.2/a.wav&command=setPlayerCmd:vol:0",
        "http://10.0.0.2/a.wav?x=1",
        "file:///etc/passwd",
        "10.0.0.2/a.wav",
        "http://10.0.0.2/" + "a" * 300,
        7,
    ],
)
async def test_url_que_nao_e_um_fluxo_nunca_chega_ao_fio(caixa, valor):
    """The value lands inside the query string of the speaker, so a value that
    carried a separator would write a second command nobody wrote in the driver.
    """
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("tocar", valor) == "invalid_value"
        assert aparelho.pedidos == []


async def test_tocar_uma_url_e_pausar_mandam_o_comando_do_protocolo(caixa):
    rotas = _fala()
    rotas.update(_rotas({f"setPlayerCmd:play:{URL}": "OK", "setPlayerCmd:pause": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("tocar", URL) is None
        assert await driver.executar("pausar") is None
    assert _comandos(aparelho) == [f"setPlayerCmd:play:{URL}", "setPlayerCmd:pause"]
    assert driver.estado().tocando is None


@pytest.mark.parametrize("valor", [None, ""])
async def test_tocar_sem_valor_retoma_o_que_estava_pausado(caixa, valor):
    """The play DP is a boolean, so play with no address is what turns it on."""
    rotas = _fala()
    rotas.update(_rotas({"setPlayerCmd:resume": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("tocar", valor) is None
    assert _comandos(aparelho) == ["setPlayerCmd:resume"]


async def test_o_mudo_a_proxima_a_anterior_e_o_preset_falam_a_api_http(caixa):
    """The mute, the next and previous track and a preset key are commands of the HTTP API of
    the module, so nothing but the HTTP surface is ever dialled.
    """
    rotas = _fala()
    rotas.update(
        _rotas(
            {
                "setPlayerCmd:mute:1": "OK",
                "setPlayerCmd:mute:0": "OK",
                "setPlayerCmd:next": "OK",
                "setPlayerCmd:prev": "OK",
                "MCUKeyShortClick:3": "OK",
            }
        )
    )
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("mudo", True) is None
        assert driver.estado().mudo is True
        assert await driver.executar("mudo", False) is None
        assert driver.estado().mudo is False
        assert await driver.executar("proxima") is None
        assert await driver.executar("anterior") is None
        assert await driver.executar("atalho", "preset:3") is None
        assert driver.estado().reproduzindo is True
    assert _comandos(aparelho) == [
        "setPlayerCmd:mute:1",
        "setPlayerCmd:mute:0",
        "setPlayerCmd:next",
        "setPlayerCmd:prev",
        "MCUKeyShortClick:3",
    ]


async def test_um_atalho_com_endereco_toca_a_radio(caixa):
    """A shortcut written as an address is a radio or a stream, and it plays like tocar."""
    rotas = _fala()
    rotas.update(_rotas({f"setPlayerCmd:play:{URL}": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("atalho", URL) is None
        assert driver.estado().reproduzindo is True
    assert _comandos(aparelho) == [f"setPlayerCmd:play:{URL}"]


async def test_o_preset_respeita_as_teclas_que_a_caixa_diz_ter(caixa):
    """The speaker says how many preset keys it has, and a key it does not have is refused
    before the wire; a box that did not say gets the ceiling of the driver.
    """
    rotas = _fala(identidade=_identidade(preset_key="6"))
    rotas.update(_rotas({"MCUKeyShortClick:6": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert await driver.executar("atalho", "preset:6") is None
        assert await driver.executar("atalho", "preset:7") == "invalid_value"
    assert _comandos(aparelho)[-1] == "MCUKeyShortClick:6"


@pytest.mark.parametrize(
    "valor",
    ["preset:0", "preset:13", "preset:", "preset:um", "3", 3, None, "ftp://10.0.0.2/a.wav"],
)
async def test_atalho_fora_do_vocabulario_nunca_chega_ao_fio(caixa, valor):
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("atalho", valor) == "invalid_value"
        assert aparelho.pedidos == []


async def test_a_caixa_so_e_escrava_quando_o_getstatusex_diz_que_esta_num_grupo(caixa):
    """Measured on 5/set/2026: a speaker idle after leaving a group keeps answering mode 99
    with group 0 and no master, and the panel refused its volume and transport as if it
    followed a master; the group field of getStatusEx is the fact, and the mode only stands
    in when the field is absent.
    """
    parada = _tocador(mode=MODO_ESCRAVO, status="stop")
    rotas = _fala(estado=parada, identidade=_identidade(group="0"))
    rotas.update(_rotas({"setPlayerCmd:vol:30": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.e_escravo() is False
        assert await driver.executar("volume", 30) is None
    # A box that says group 1 and names its master is a slave whatever the mode says.
    grupo = _identidade(group="1", master_uuid=OUTRA_IDENTIDADE)
    rotas = _fala(estado=_tocador(mode=MODO_DE_REDE), identidade=grupo)
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.e_escravo() is True
        assert await driver.executar("volume", 30) == "nao_suportado"


async def test_a_entrada_fisica_e_a_volta_para_a_rede_sao_um_switchmode(caixa):
    """The physical input and the way back to the network are both a switchmode of the HTTP
    API, with the name the module gives each input.
    """
    rotas = _fala()
    rotas.update(
        _rotas({"setPlayerCmd:switchmode:wifi": "OK", "setPlayerCmd:switchmode:line-in": "OK"})
    )
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert await driver.executar("fonte", "line-in") is None
        assert _comandos(aparelho)[-1] == "setPlayerCmd:switchmode:line-in"
        assert driver.estado().fonte == "line-in"
        assert await driver.executar("fonte", "wifi") is None
        assert _comandos(aparelho)[-1] == "setPlayerCmd:switchmode:wifi"
        assert driver.estado().fonte == "wifi"


async def test_agrupar_desagrupar_e_o_volume_de_um_escravo_falam_o_protocolo(caixa):
    """Joining is asked of the slave, ungrouping and the volume of a slave are
    asked of the master.
    """
    entrada = f"ConnectMasterAp:JoinGroupMaster:eth{IP_DO_MESTRE}:wifi0.0.0.0"
    volume = f"multiroom:SlaveVolume:{IP_DO_ESCRAVO}:40"
    rotas = _fala()
    rotas.update(_rotas({entrada: "OK", DESFAZ_GRUPO: "OK", volume: "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.entrar_no_grupo(IP_DO_MESTRE) is None
        assert await driver.volume_de_escravo(IP_DO_ESCRAVO, 40) is None
        assert await driver.executar("agrupar", None) is None
    assert _comandos(aparelho) == [entrada, volume, DESFAZ_GRUPO]


@pytest.mark.parametrize("valor", ["caixa.local", "http://10.0.0.9", "10.0.0.9:8080", 10, "", None])
async def test_um_grupo_so_e_formado_com_um_endereco_literal(caixa, valor):
    """Only an IP literal reaches a device, so the hub is never a resolver and
    never a proxy into the LAN of the client.
    """
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert await driver.entrar_no_grupo(valor) == "invalid_value"
        assert await driver.volume_de_escravo(valor, 30) == "invalid_value"
        assert not [c for c in _comandos(aparelho) if "Group" in c or "Slave" in c]


async def test_o_grupo_lido_e_chaveado_por_uuid_e_recusa_endereco_que_nao_e_ip(caixa):
    """The key of a member is its uuid; an entry naming a host instead of an
    address would make the hub reach whatever the speaker wrote.
    """
    lista = json.dumps(
        {
            "slaves": 4,
            "slave_list": [
                {"uuid": IDENTIDADE, "ip": IP_DO_ESCRAVO, "name": "Sala"},
                {"uuid": IDENTIDADE, "ip": "10.0.0.99", "name": "Sala de novo"},
                {"uuid": OUTRA_IDENTIDADE, "ip": "caixa.local", "name": "Quarto"},
                {"uuid": "", "ip": "10.0.0.12", "name": "Sem identidade"},
            ],
        }
    )
    async with ServidorHttp(_fala(**{PEDE_ESCRAVOS: lista})) as aparelho:
        driver = caixa(aparelho)
        grupo = await driver.ler_grupo()
    assert grupo is not None
    assert grupo.escravos == (Escravo(IDENTIDADE, IP_DO_ESCRAVO, "Sala"),)


async def test_uma_lista_de_escravos_sem_fim_tem_teto(caixa):
    """A speaker on the LAN must not be able to make the daemon hold what it likes."""
    membros = [{"uuid": f"{numero:032x}", "ip": "10.0.0.20"} for numero in range(500)]
    lista = json.dumps({"slave_list": membros})
    async with ServidorHttp(_fala(**{PEDE_ESCRAVOS: lista})) as aparelho:
        driver = caixa(aparelho)
        grupo = await driver.ler_grupo()
    assert grupo is not None
    assert len(grupo.escravos) == linkplay.ESCRAVOS_MAXIMO


async def test_a_caixa_que_nao_responde_e_eq_offline_e_nunca_uma_excecao(caixa):
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
    assert await driver.executar("volume", 30) == "eq_offline"
    assert await driver.executar("mudo", True) == "eq_offline"
    assert await driver.ler_grupo() is None
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_cadastro_sem_ip_nunca_fala_com_ninguem(caixa):
    """The hub only talks to an address somebody registered, never to a resolver default."""
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho, ip="")
        assert await driver.executar("volume", 30) == "eq_offline"
        assert await driver.executar("mudo", True) == "eq_offline"
        await driver.atualizar()
        assert aparelho.pedidos == []


@pytest.mark.parametrize("corpo", ["Failed", "unknown command", ""])
async def test_uma_resposta_que_nao_e_ok_e_erro_aparelho(caixa, corpo):
    """A command the speaker refused must not read as done, or the panel shows a volume the
    speaker never took.
    """
    rotas = _fala()
    rotas.update(_rotas({"setPlayerCmd:vol:50": corpo}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("volume", 50) == "erro_aparelho"


@pytest.mark.parametrize("resposta", ["nao e json", "[]", '"texto"'])
async def test_uma_resposta_que_nao_e_um_objeto_nao_derruba_o_poll(caixa, resposta):
    async with ServidorHttp(_fala(estado=resposta)) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_uma_resposta_gigante_nao_enche_a_memoria(caixa):
    """A speaker on the LAN must not be able to make the daemon buffer without bound."""
    enorme = json.dumps({"uuid": IDENTIDADE, "lixo": "x" * (linkplay.CORPO_MAXIMO * 2)})
    async with ServidorHttp(_fala(identidade=enorme)) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.identidade_do_aparelho() is None


@pytest.mark.parametrize(
    "acao", ["ligar", "desligar", "comando_extra", "tecla", "formatar_o_disco"]
)
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(caixa, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar(acao, 50) == "nao_suportado"
        assert aparelho.pedidos == []


async def test_a_caixa_nao_pareia_e_o_contrato_nao_pede_pareamento(caixa):
    """The base refuses the inherited autenticar only when the manifest declares
    an auth, and this protocol declares none.
    """
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        assert await driver.autenticar() == "pareado"
        assert aparelho.pedidos == []


# A recording of two real speakers, taken on 2026-09-03 from firmware 4.6, with the uuid
# replaced: an identity of a real installation does not enter a public repository. Everything
# else is byte for byte what the firmware answered, including the fact that every field is
# text, that the metadata is hexadecimal, and that the mode of a streaming service is a
# number the input table does not name.
GRAVADO_IDENTIDADE = json.dumps(
    {
        "uuid": IDENTIDADE,
        "DeviceName": "Sala",
        "GroupName": "Sala",
        "firmware": "4.6.629929",
        "project": "uyesee-i50",
        "group": "0",
        "plm_support": "0x6",
        "preset_key": "9",
    }
)
GRAVADO_TOCANDO = json.dumps(
    {
        "status": "play",
        "vol": "100",
        "mute": "0",
        "mode": "31",
        "Title": "506F656D61",
        "curpos": "22626",
        "totlen": "262533",
    }
)
GRAVADO_PAUSADO = json.dumps(
    {
        "status": "pause",
        "vol": "15",
        "mute": "0",
        "mode": "10",
        "Title": "5065727468",
        "curpos": "70373",
        "totlen": "0",
    }
)


async def test_gravacao_de_caixa_real_tocando_um_servico_de_streaming(caixa):
    """A recorded speaker on mode 31, which the input table does not name: it is the network
    side of the speaker, and the title is the hexadecimal the firmware writes.

    Reading an unnamed mode as no input would take the input away from the panel every
    time somebody plays from a streaming service, which is the normal way these are used.
    """
    async with ServidorHttp(
        _fala(estado=GRAVADO_TOCANDO, identidade=GRAVADO_IDENTIDADE)
    ) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.volume == 100
    assert estado.mudo is False
    assert estado.fonte == ENTRADA_DE_REDE
    assert estado.tocando == "Poema"
    # The mask of the real box: it has a line input and bluetooth, and no usb and no optical.
    assert estado.fontes == (ENTRADA_DE_REDE, "line-in", "bluetooth")


async def test_gravacao_de_caixa_real_pausada_nao_reporta_tocando(caixa):
    """A recorded speaker paused on a stream whose length the firmware writes as zero.

    The reproduzindo is read from this, so a paused speaker that still carries the
    title of what it was playing must not read as playing.
    """
    async with ServidorHttp(
        _fala(estado=GRAVADO_PAUSADO, identidade=GRAVADO_IDENTIDADE)
    ) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.volume == 15
    assert estado.tocando is None


async def test_resposta_partida_em_dois_segmentos_e_lida_inteira(caixa):
    """The driver reads what the speaker answered, not what fit in one segment.

    One read of the stream returns only what the buffer already holds, so a status
    object that arrived in two TCP segments stopped being json and a speaker answering
    perfectly on a busy wifi read as broken.
    """
    async with ServidorHttp(
        _fala(estado=GRAVADO_TOCANDO, identidade=GRAVADO_IDENTIDADE), partir=True
    ) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.volume == 100
    assert estado.tocando == "Poema"
    assert driver.identidade_do_aparelho() == IDENTIDADE


async def test_outra_caixa_no_mesmo_endereco_e_recusada_e_nao_comandada(caixa):
    """Identity is the uuid, and the address is only where it answered today.

    Asking once and never again left the hub commanding whatever box now holds the
    address, under the name of this block, for as long as the daemon ran: a lease that moved
    would have the volume of somebody else's speaker following this block.
    """
    async with ServidorHttp(_fala()) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.identidade_do_aparelho() == IDENTIDADE
        assert driver.estado().online is True
        # The lease moved and another speaker answers at that address.
        outra = json.dumps({"uuid": OUTRA_IDENTIDADE, "plm_support": MASCARA})
        aparelho.rotas.update(_rotas({PEDE_IDENTIDADE: outra}))
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        # The speaker comes back by its identity, so ours returning is adopted
        # again while the stranger never was.
        aparelho.rotas.update(_rotas({PEDE_IDENTIDADE: _identidade()}))
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.identidade_do_aparelho() == IDENTIDADE


async def test_o_play_prende_o_transporte_no_cache(caixa):
    """The bus publishes from the cache every second, before the 1.5 s reread.

    A play that pinned nothing let that tick republish the old transport, so reproduzindo fell
    back to false one second after the command the speaker had accepted.
    """
    rotas = _fala(estado=_tocador(status="pause"))
    rotas.update(_rotas({"setPlayerCmd:resume": "OK", "setPlayerCmd:pause": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.estado().reproduzindo is False
        assert await driver.executar("tocar") is None
        assert driver.estado().reproduzindo is True, "play did not pin the transport"
        assert await driver.executar("pausar") is None
        assert driver.estado().reproduzindo is False


async def test_marcar_grupo_apaga_o_veredito_de_que_a_caixa_tinha_saido(caixa):
    """The owner of the group logic has just said this speaker is a member, and that settles
    it: a verdict left over from an earlier group must not tear down the group just formed.
    """
    escravo = _tocador(mode=MODO_ESCRAVO)
    async with ServidorHttp(_fala(estado=escravo)) as aparelho:
        driver = caixa(aparelho)
        driver.marcar_grupo(True)
        await driver.atualizar()
        assert driver.e_escravo() is True
        # Two polls out of the multiroom mode: the driver decides the group dissolved.
        aparelho.rotas.update(_rotas({PEDE_ESTADO: _tocador()}))
        await driver.atualizar()
        await driver.atualizar()
        assert driver.saiu_do_grupo() is True
        # The owner forms a new group with it, which invalidates that verdict.
        driver.marcar_grupo(True)
        assert driver.saiu_do_grupo() is False


async def test_um_json_fundo_demais_e_erro_aparelho_e_nunca_uma_excecao(caixa):
    """A body nested deeper than the parser recurses is an answer the speaker chose, and it
    leaves the poll, the group reading and the finding of the sweep the way any bad answer
    does, never as an exception out of the driver.
    """
    fundo = "[" * 60_000
    async with ServidorHttp(_fala(estado=fundo)) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "erro_aparelho"
    rotas = _fala()
    rotas.update(_rotas({PEDE_ESCRAVOS: fundo}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert await driver.ler_grupo() is None
    async with ServidorHttp(_fala(identidade=fundo)) as aparelho:
        caixa(aparelho)
        assert await LinkPlay.identificar("127.0.0.1") is None


async def test_trocar_a_entrada_solta_o_titulo_da_rede_do_cache(caixa):
    """The firmware keeps the title of the last network source, so the cache of a
    speaker switched to the line input would show the last track of the radio until the next
    poll, and the bus publishes from that cache once a second.
    """
    rotas = _fala()
    rotas.update(_rotas({"setPlayerCmd:switchmode:line-in": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.estado().tocando == f"{TITULO} - {ARTISTA}"
        assert await driver.executar("fonte", "line-in") is None
    assert driver.estado().fonte == "line-in"
    assert driver.estado().tocando is None


async def test_um_fluxo_ou_um_preset_prende_a_rede_no_cache_e_solta_o_titulo_antigo(caixa):
    """A stream or a preset plays over the network whatever input the speaker was on, and the
    title it will show is the poll's to read, never the one of what played before.
    """
    rotas = _fala(identidade=_identidade(preset_key="6"))
    rotas.update(_rotas({f"setPlayerCmd:play:{URL}": "OK", "MCUKeyShortClick:2": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.estado().tocando == f"{TITULO} - {ARTISTA}"
        assert await driver.executar("tocar", URL) is None
        estado = driver.estado()
        assert (estado.fonte, estado.reproduzindo, estado.tocando) == ("wifi", True, None)
        aparelho.rotas.update(
            _fala(estado=_tocador(mode=MODO_DE_LINHA), identidade=_identidade(preset_key="6"))
        )
        await driver.atualizar()
        assert driver.estado().fonte == "line-in"
        assert await driver.executar("atalho", "preset:2") is None
        estado = driver.estado()
        assert (estado.fonte, estado.reproduzindo, estado.tocando) == ("wifi", True, None)


async def test_uma_caixa_que_declara_zero_teclas_de_preset_recusa_todo_preset(caixa):
    """Preset_key 0 is an answer, not a silence: the speaker said it has no key, and the
    ceiling of the driver only stands in for a box that did not say.
    """
    async with ServidorHttp(_fala(identidade=_identidade(preset_key="0"))) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert await driver.executar("atalho", "preset:1") == "invalid_value"
    assert not any(comando.startswith("MCUKeyShortClick") for comando in _comandos(aparelho))


async def test_o_campo_group_tambem_pede_dois_polls_para_dizer_que_a_caixa_saiu(caixa):
    """The group field of getStatusEx is the primary signal now, and the reconciliation of
     (one poll out is a hiccup, two are a fact) has to hold on it as it held on the
    mode.
    """
    grupo = _identidade(group="1", master_uuid=OUTRA_IDENTIDADE)
    async with ServidorHttp(_fala(identidade=grupo)) as aparelho:
        driver = caixa(aparelho)
        driver.espelhar("Faixa do mestre")
        await driver.atualizar()
        assert driver.e_escravo() is True
        assert driver.estado().tocando == "Faixa do mestre"
        aparelho.rotas.update(_fala(identidade=_identidade(group="0")))
        await driver.atualizar()
        assert driver.saiu_do_grupo() is False, "one poll out of the group is a hiccup"
        assert driver.e_escravo() is True
        await driver.atualizar()
    assert driver.saiu_do_grupo() is True
    assert driver.e_escravo() is False
    assert driver.estado().tocando == f"{TITULO} - {ARTISTA}"


async def test_um_fluxo_num_endereco_ipv6_chega_ao_fio_como_foi_escrito(caixa):
    """The bytes the driver checks are the bytes the speaker reads: the client must not
    rewrite the brackets of an IPv6 address on its own.
    """
    url = "http://[fe80::1]/a.wav"
    rotas = _fala()
    rotas.update(_rotas({f"setPlayerCmd:play:{url}": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.executar("tocar", url) is None
    assert _comandos(aparelho)[-1] == f"setPlayerCmd:play:{url}"


async def test_parar_solta_o_fluxo_e_pausar_apenas_pausa(caixa):
    """A pause on a stream keeps the speaker connected to the station, so a radio needs the
    stop of the protocol; both leave the transport off and clear the title of the cache.
    """
    rotas = _fala()
    rotas.update(_rotas({"setPlayerCmd:stop": "OK", "setPlayerCmd:pause": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        assert driver.estado().tocando == f"{TITULO} - {ARTISTA}"
        assert await driver.executar("parar") is None
        estado = driver.estado()
        assert (estado.reproduzindo, estado.tocando) == (False, None)
        assert await driver.executar("pausar") is None
    assert _comandos(aparelho)[-2:] == ["setPlayerCmd:stop", "setPlayerCmd:pause"]


async def test_parar_de_um_escravo_nunca_chega_ao_fio(caixa):
    """The transport of a group is the master's, and stop is transport."""
    grupo = _identidade(group="1", master_uuid=OUTRA_IDENTIDADE)
    async with ServidorHttp(_fala(identidade=grupo)) as aparelho:
        driver = caixa(aparelho)
        await driver.atualizar()
        antes = len(aparelho.pedidos)
        assert await driver.executar("parar") == "nao_suportado"
        assert len(aparelho.pedidos) == antes


def test_a_caixa_sugere_radios_que_o_cadastro_aceita():
    """A shortcut is a URL the speaker fetches by itself, which nobody guesses, so
    the driver offers examples and every one of them is an item the registration accepts and
    an address the guard of the wire lets through.
    """
    sugestoes = por_lista(LinkPlay.MANIFESTO)
    assert set(sugestoes) == {"atalhos"}
    atalhos = sugestoes["atalhos"]
    assert len(atalhos) >= 3
    for sugestao in atalhos:
        assert item_valido(sugestao.rotulo, sugestao.valor)
        endereco = sugestao.valor.startswith("http://")
        assert endereco or linkplay._preset_de(sugestao.valor, linkplay.PRESET_MAXIMO) is not None
        if endereco:
            assert linkplay._url_valida(sugestao.valor)
    # The inputs of a box are the ones its plm_support declares, read at every poll, so a
    # suggested list would replace that true list with a guess.
    assert "entradas" not in sugestoes


async def test_o_nome_do_atalho_e_o_que_toca_ate_a_caixa_nomear_o_fluxo(caixa):
    """A radio is a raw stream and this firmware answers an empty Title for one until the
    station sends metadata, which many never do. The hub asked for the stream by a shortcut
    the integrator named, so that name is what plays until the speaker names something.
    """
    radio = Item("Groove Salad", URL)
    sem_titulo = _tocador(Title="", Artist="")
    rotas = _fala(estado=sem_titulo)
    rotas.update(_rotas({f"setPlayerCmd:play:{URL}": "OK", "setPlayerCmd:stop": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho, listas={"atalhos": (radio,)})
        await driver.atualizar()
        assert driver.estado().tocando is None
        assert await driver.executar("atalho", URL) is None
        assert driver.estado().tocando == "Groove Salad"
        # The poll finds no title of its own and keeps the name of what was asked for.
        await driver.atualizar()
        assert driver.estado().tocando == "Groove Salad"
        # A station that starts naming what it plays takes the line over.
        aparelho.rotas.update(_fala(estado=_tocador()))
        await driver.atualizar()
        assert driver.estado().tocando == f"{TITULO} - {ARTISTA}"
        # And stopping lets go of both.
        aparelho.rotas.update(_fala(estado=sem_titulo))
        assert await driver.executar("parar", None) is None
        await driver.atualizar()
    assert driver.estado().tocando is None


async def test_um_atalho_de_endereco_que_nao_esta_na_lista_nao_inventa_nome(caixa):
    """The name only ever comes from the list of the registration, so a stream nobody named
    plays with no title instead of with an address on the line of the panel.
    """
    rotas = _fala(estado=_tocador(Title="", Artist=""))
    rotas.update(_rotas({f"setPlayerCmd:play:{URL}": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho, listas={"atalhos": (Item("Outra", "http://10.0.0.3/x.mp3"),)})
        assert await driver.executar("tocar", URL) is None
    assert driver.estado().tocando is None


async def test_o_mestre_tira_um_membro_sem_derrubar_o_grupo(caixa):
    """A group is a master and the members the customer chose, so taking one out
    is its own move on the master, and never the Ungroup that takes everyone down.
    """
    rotas = _fala()
    rotas.update(_rotas({f"multiroom:SlaveKickout:{IP_DO_ESCRAVO}": "OK"}))
    async with ServidorHttp(rotas) as aparelho:
        driver = caixa(aparelho)
        assert await driver.tirar_do_grupo(IP_DO_ESCRAVO) is None
        assert await driver.tirar_do_grupo("caixa.local") == "invalid_value"
    assert _comandos(aparelho) == [f"multiroom:SlaveKickout:{IP_DO_ESCRAVO}"]
