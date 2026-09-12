# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 8 and 9 under attack on the socket of the bus: every rule here is an attempt to
break it, never a happy path.

The bus is the door of the bridge into the whole installation, it carries no session and it
is exposed on the LAN of the customer with no TLS, so what it refuses is what protects every
number of every licence. Attacked here: a frame that arrives before the auth, a token
smuggled in the URL, a wrong token, a token outside ASCII, a licence this hub does not have,
silence past the deadline, a set on a data point nobody may set, a set on a data point of the
OTHER product, a set on a number nobody occupies, a command channel fed control characters and
a page of text, a page of another site opening the socket, a Host that is not this hub, the
api_token leaking back into a frame, a flood of frames, an oversized frame, a client that
disappears in the middle of a verification and a licence that left the installation.
"""

import asyncio
import base64
import contextlib
import json
import os
import secrets

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError

from iphub.api.comum import SEGREDOS
from iphub.config import Cadastro, Config, Item, Licenca
from iphub.dpbus import socket as modulo_socket
from iphub.dpbus.socket import BARRAMENTO
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import MODOS_AR, VENTOS, Manifesto
from iphub.portao import CABECALHOS, SERVIDOR
from iphub.segredos import Segredos

CAMINHO = "/dpbus"
TIPO = "multiroom_falso"
TIPO_AR = "ar_falso"
TOKEN = "token-de-maquina-so-deste-teste"
IP_1 = "192.0.2.11"
IP_AR = "192.0.2.21"
ALHEIA = "http://evil.example.com"

AV = "av1"
AR = "ar1"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")
CAPACIDADES_AR = ("ligar", "desligar", "temperatura", "modo", "vento")

# The numbers, written by hand on purpose.
LIGADO_3, LIGADO_5 = 103, 105
NIVEL_1, NIVEL_3 = 121, 123
CENA_AV, GRUPO, COMANDO, ONLINE_AV = 141, 142, 143, 144
MUDOS, ENTRADAS, MODOS, TITULOS, PERFIS_1, NOMES_CENAS_AV = 145, 146, 147, 148, 149, 154
TEMPERATURA_1, AR_LIGADO_2 = 102, 106
CENA_AR, ONLINE_AR, NOMES_MAQUINAS, NOMES_CENAS_AR = 171, 172, 173, 174

FECHAMENTO_NAO_AUTENTICADO = 4401
FECHAMENTO_QUADRO_GRANDE = 1009
PRAZO_AUTH_S = 5.0
RELEITURA_S = 1.5
JANELA_A_S = 2.0


def _textos(descricao: str) -> dict[str, dict[str, str]]:
    return {"pt": {"descricao": descricao}, "en": {"descricao": descricao}}


def _manifesto() -> Manifesto:
    return Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Caixa", "en": "Speaker"},
        categoria="multiroom",
        capacidades=CAPACIDADES,
        textos=_textos("Caixa de teste"),
    )


def _manifesto_ar() -> Manifesto:
    return Manifesto(
        tipo=TIPO_AR,
        rotulo={"pt": "Ar", "en": "Air conditioner"},
        categoria="ar_condicionado",
        capacidades=CAPACIDADES_AR,
        modos=MODOS_AR,
        ventos=VENTOS,
        textos=_textos("Ar de teste"),
    )


def _fabrica() -> type[Driver]:
    """A speaker that writes down every command, so a test proves nothing reached it."""

    class Falsa(Driver):
        MANIFESTO = _manifesto()
        instancias: list["Falsa"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self._defina(online=True, volume=20, fonte="wifi", tocando=None)
            type(self).instancias.append(self)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            self.chamadas.append((acao, valor))
            if acao == "volume":
                self._defina(volume=valor)
            return None

        async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
            self.chamadas.append(("entrar_no_grupo", ip_do_mestre))
            return None

        async def desfazer_grupo(self) -> str | None:
            self.chamadas.append(("desfazer_grupo", None))
            return None

        async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
            self.chamadas.append(("volume_de_escravo", (ip, valor)))
            return None

        async def ler_grupo(self) -> object:
            return None

        def marcar_grupo(self, dentro: bool) -> None:
            pass

        def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
            pass

        def e_escravo(self) -> bool:
            return False

        def saiu_do_grupo(self) -> bool:
            return False

    return Falsa


def _fabrica_ar() -> type[Driver]:
    """An air conditioner that writes down every command, for the same proof."""

    class Ar(Driver):
        MANIFESTO = _manifesto_ar()
        instancias: list["Ar"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self._defina(online=True, ligado=False, temperatura=24, modo="frio", vento="auto")
            type(self).instancias.append(self)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            self.chamadas.append((acao, valor))
            return None

    return Ar


@pytest.fixture
def caixas() -> type[Driver]:
    return _fabrica()


@pytest.fixture
def ares() -> type[Driver]:
    return _fabrica_ar()


@pytest.fixture
async def cliente(fabrica_cliente, agenda, caixas, ares):
    """One speaker in number 1 of a licence of audio and video, numbers 2 to 12 empty, one
    air conditioner in number 1 of a licence of air, and a clock the test moves by hand.
    """
    sala = Cadastro(
        identidade="uuid-1",
        tipo=TIPO,
        nome="Sala",
        ip=IP_1,
        listas={"entradas": (Item(rotulo="Wi-Fi", valor="wifi"),)},
    )
    quarto = Cadastro(identidade="uuid-ar", tipo=TIPO_AR, nome="Quarto", ip=IP_AR)
    return await fabrica_cliente(
        config=Config(
            equipamentos=(sala, quarto),
            licencas=(Licenca(id=AV, produto="av"), Licenca(id=AR, produto="ar")),
            numeros={AV: ("uuid-1",), AR: ("uuid-ar",)},
        ),
        segredos=Segredos(api_token=TOKEN),
        catalogo={TIPO: caixas, TIPO_AR: ares},
        dormir=agenda.dormir,
        agora=agenda,
    )


def _set(dpid: int, valor: object, identificador: int = 1) -> str:
    return json.dumps({"t": "set", "id": identificador, "dpid": dpid, "v": valor})


def _consulta(identificador: int = 1) -> str:
    return json.dumps({"t": "consulta", "id": identificador})


def _auth(token: str = TOKEN, licenca: str = AV) -> str:
    return json.dumps({"t": "auth", "token": token, "licenca": licenca})


async def _abrir(cliente, licenca: str = AV, token: str = TOKEN, **extras):
    ws = await cliente.ws_connect(CAMINHO, **extras)
    await ws.send_str(_auth(token, licenca))
    return ws


async def _tudo(ws) -> list[dict]:
    quadros = []
    while True:
        try:
            mensagem = await ws.receive(timeout=0.05)
        except TimeoutError:
            return quadros
        if not isinstance(mensagem.data, str):
            return quadros
        quadros.append(json.loads(mensagem.data))


async def _fechado_com_4401(ws) -> None:
    mensagem = await ws.receive(timeout=2)
    assert mensagem.type is WSMsgType.CLOSE, mensagem
    assert ws.close_code == FECHAMENTO_NAO_AUTENTICADO


def _nada_chegou(*fabricas: type[Driver]) -> bool:
    return all(instancia.chamadas == [] for f in fabricas for instancia in f.instancias)


async def test_nenhum_quadro_antes_do_auth_e_honrado(cliente, caixas):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_set(NIVEL_1, 99))
    # The FIRST frame authenticates; a bus that ran a set before it would hand
    # the volume of the whole house to anybody who reached the port.
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas)


async def test_uma_consulta_antes_do_auth_nao_e_respondida(cliente):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_consulta())
    # The snapshot is the state of every number of a licence, and a socket that has not
    # said who it is gets nothing, not even a read.
    await _fechado_com_4401(ws)


async def test_um_token_na_url_nao_autentica(cliente, caixas, agenda):
    # A query string is written into every access log and into the history of whoever
    # pasted it, so the token only ever travels inside the first frame.
    ws = await cliente.ws_connect(f"{CAMINHO}?token={TOKEN}&licenca={AV}")
    await ws.send_str(_set(NIVEL_1, 99))
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas)
    outro = await cliente.ws_connect(f"{CAMINHO}?token={TOKEN}&licenca={AV}")
    assert await agenda.soltar(PRAZO_AUTH_S) == 1
    await _fechado_com_4401(outro)


@pytest.mark.parametrize(
    "token", ["", "outro-token", TOKEN + "x", TOKEN[:-1], TOKEN.upper(), "x" * 4000]
)
async def test_um_token_que_nao_casa_fecha_4401(cliente, token):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(token))
    await _fechado_com_4401(ws)


@pytest.mark.parametrize(
    "bruto",
    [
        {"t": "auth", "licenca": AV},
        {"t": "auth", "token": TOKEN},
        {"t": "auth", "token": TOKEN, "licenca": ""},
        {"t": "auth", "token": TOKEN, "licenca": 7},
        {"t": "auth", "token": TOKEN, "licenca": [AV]},
        {"t": "auth", "token": None, "licenca": AV},
        {"t": "auth", "token": [TOKEN], "licenca": AV},
        {"t": "set", "token": TOKEN, "licenca": AV},
    ],
)
async def test_um_auth_sem_token_ou_sem_licenca_fecha_4401(cliente, caixas, bruto):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(json.dumps(bruto))
    # The first frame names the token AND the licence; half of it names
    # nothing, and a bus that guessed the missing half would hand out a licence by luck.
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas)


@pytest.mark.parametrize("token", ["tökén-de-maquina", "\ud800", "senha ção"])
async def test_um_token_fora_do_ascii_fecha_4401_em_vez_de_estourar(cliente, token):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(token))
    # The api_token is a token_urlsafe, which is ASCII, and comparing a non ASCII string
    # in constant time raises instead of answering that it does not match; a raise here would
    # be a traceback in the log for every probe and a socket that closes with no code.
    await _fechado_com_4401(ws)
    outro = await _abrir(cliente)
    await outro.send_str(_consulta())
    assert [q["t"] for q in await _tudo(outro)] == ["snapshot"]
    await outro.close()


async def test_o_token_e_comparado_em_tempo_constante(cliente, monkeypatch):
    """The shape of the comparison is what a test can assert: the token of the frame reaches
    the constant time primitive of the standard library, whole, against the token of the hub.

    Comparing with == hands whoever measures the answer the length of the common prefix,
    and this token is the machine credential of the whole bus; timing cannot be measured in a
    test, so the test proves the primitive is the one that judges.
    """
    comparacoes: list[tuple[object, object]] = []
    original = secrets.compare_digest

    def espiao(a: object, b: object) -> bool:
        comparacoes.append((a, b))
        return original(a, b)

    monkeypatch.setattr(modulo_socket.secrets, "compare_digest", espiao)
    tentado = "token-de-maquina-so-deste-testx"
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(tentado))
    await _fechado_com_4401(ws)
    assert comparacoes == [(tentado, TOKEN)]
    certo = await _abrir(cliente)
    await certo.send_str(_consulta())
    assert [q["t"] for q in await _tudo(certo)] == ["snapshot"]
    assert comparacoes[-1] == (TOKEN, TOKEN)
    await certo.close()


@pytest.mark.parametrize("licenca", ["av9", "AV1", " av1", "ar", "av1/", "x" * 41])
async def test_uma_licenca_que_o_hub_nao_tem_fecha_4401_antes_de_qualquer_dado_sair(
    cliente, caixas, ares, licenca
):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(TOKEN, licenca))
    await ws.send_str(_consulta())
    await ws.send_str(_set(NIVEL_1, 99))
    # The right token with the wrong licence is a bridge of another hub, or a probe that
    # knows the token and is looking for what this hub has; the answer is the close and no
    # frame, so the probe does not even learn which licences exist.
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas, ares)
    assert cliente.app[BARRAMENTO].ouvintes == 0


async def test_sem_auth_em_cinco_segundos_fecha_4401(cliente, agenda):
    ws = await cliente.ws_connect(CAMINHO)
    await agenda.girar()
    # A socket that says nothing holds a connection of the daemon for free, and the
    # deadline is what takes it back.
    assert agenda.presas(PRAZO_AUTH_S) == 1
    assert await agenda.soltar(PRAZO_AUTH_S) == 1
    await _fechado_com_4401(ws)


async def test_um_auth_certo_depois_do_prazo_nao_salva_o_socket(cliente, agenda):
    ws = await cliente.ws_connect(CAMINHO)
    await agenda.girar()
    await agenda.soltar(PRAZO_AUTH_S)
    await ws.send_str(_auth())
    await _fechado_com_4401(ws)


@pytest.mark.parametrize(
    ("licenca", "dpid", "valor"),
    [
        (AV, ONLINE_AV, 1),
        (AV, MUDOS, 1),
        (AV, ENTRADAS, "1=1"),
        (AV, MODOS, "1=1"),
        (AV, TITULOS, "1=Musica"),
        (AV, PERFIS_1, "1|au|Sala||||N"),
        (AV, NOMES_CENAS_AV, '{"c":[]}'),
        (AR, ONLINE_AR, 1),
        (AR, NOMES_MAQUINAS, '{"m":[]}'),
        (AR, NOMES_CENAS_AR, '{"c":[]}'),
    ],
)
async def test_um_set_num_dp_de_so_report_e_recusado(cliente, caixas, ares, licenca, dpid, valor):
    ws = await _abrir(cliente, licenca)
    await ws.send_str(_set(dpid, valor))
    # The chip never echoes and a report is only ever born of real state; a
    # set accepted here would publish a speaker as online because somebody asked for it.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "dp_somente_leitura"}]
    assert _nada_chegou(caixas, ares)
    await ws.close()


@pytest.mark.parametrize(
    ("licenca", "dpid", "valor"),
    [
        (AV, CENA_AR, 1),
        (AV, ONLINE_AR, 1),
        (AV, NOMES_MAQUINAS, '{"m":[]}'),
        (AR, LIGADO_5, True),
        (AR, CENA_AV, 1),
        (AR, GRUPO, 1),
        (AR, COMANDO, "1:ligar"),
        (AR, ONLINE_AV, 1),
    ],
)
async def test_um_set_num_dp_do_outro_produto_e_dp_desconhecido(
    cliente, caixas, ares, licenca, dpid, valor
):
    ws = await _abrir(cliente, licenca)
    await ws.send_str(_set(dpid, valor))
    # A licence is a device of ONE product and its socket speaks the table of
    # that product only; a number the other table does not have (the fifth of every machine
    # of air is free, and nothing of air lives past 140) is refused as unknown, so a set
    # never lands on a number of a licence the bridge did not name.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "dp_desconhecido"}]
    assert _nada_chegou(caixas, ares)
    await ws.close()


async def test_um_dpid_e_lido_contra_a_tabela_do_produto_da_licenca(cliente, caixas, ares):
    # 102 is the power of equipment 2 on audio and video and the setpoint of machine 1
    # on air, so the same frame is judged by what it means on the licence that received it;
    # a bus that read it by the other table would switch a machine with a temperature.
    do_av = await _abrir(cliente, AV)
    do_ar = await _abrir(cliente, AR)
    await do_av.send_str(_set(TEMPERATURA_1, 22))
    await do_ar.send_str(_set(TEMPERATURA_1, True))
    assert await _tudo(do_av) == [{"t": "ack", "id": 1, "ok": False, "code": "valor_invalido"}]
    assert await _tudo(do_ar) == [{"t": "ack", "id": 1, "ok": False, "code": "valor_invalido"}]
    assert _nada_chegou(caixas, ares)
    await do_av.close()
    await do_ar.close()


@pytest.mark.parametrize(
    ("licenca", "dpid", "valor"),
    [(AV, NIVEL_3, 50), (AV, LIGADO_3, True), (AR, AR_LIGADO_2, True)],
)
async def test_um_set_num_numero_que_ninguem_ocupa_e_recusado(
    cliente, caixas, ares, licenca, dpid, valor
):
    ws = await _abrir(cliente, licenca)
    await ws.send_str(_set(dpid, valor))
    # An empty number reaches no equipment, and answering ok for it would tell the bridge
    # that a number nobody registered took the command.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "numero_offline"}]
    assert _nada_chegou(caixas, ares)
    await ws.close()


@pytest.mark.parametrize(
    "valor",
    [
        "1:extra:preset\x00",
        "1:mudo\n",
        "1:extra:\x1b[2J",
        "1:extra:x\x7f",
        "1:extra:" + "x" * 300,
        "1:extra:" + "x" * 65,
        "1:extra:" + "ç" * 200,
        "1:extra:\ud800",
        "",
        7,
        True,
        None,
        ["1:mudo"],
    ],
)
async def test_o_canal_de_comando_recusa_controle_e_texto_longo_sem_tocar_no_driver(
    cliente, caixas, valor
):
    ws = await _abrir(cliente)
    await ws.send_str(_set(COMANDO, valor))
    # The command channel is the one string a bridge writes, and what the driver puts on
    # the wire of the device comes from it; a control character or a page of text is refused
    # by the contract before any parser, any lock and any driver sees it.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "valor_invalido"}]
    assert _nada_chegou(caixas)
    await ws.close()


async def test_um_origin_de_outro_site_nao_abre_o_socket(cliente, caixas):
    # A page of another site loaded by whoever is on the network would open
    # this socket with the browser of the integrator if the Origin rule did not stop it.
    with pytest.raises(WSServerHandshakeError) as erro:
        await cliente.ws_connect(CAMINHO, headers={"Origin": ALHEIA})
    assert erro.value.status == 403
    for nome, valor in CABECALHOS.items():
        assert erro.value.headers.get(nome) == valor, nome
    assert _nada_chegou(caixas)


async def test_um_host_fora_da_lista_nao_abre_o_socket(cliente):
    # The Host rule is what closes DNS rebinding without the attacker being on
    # the LAN, and the bus is not outside it just because it is not under /api/.
    with pytest.raises(WSServerHandshakeError) as erro:
        await cliente.ws_connect(CAMINHO, headers={"Host": "evil.example.com"})
    assert erro.value.status == 421


async def test_o_aperto_de_mao_leva_os_quatro_cabecalhos(cliente):
    resposta = await cliente.get(
        CAMINHO,
        headers={
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode(),
            "Sec-WebSocket-Version": "13",
        },
    )
    assert resposta.status == 101
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome
    assert resposta.headers.get("Server") == SERVIDOR


async def test_o_api_token_nunca_aparece_num_quadro_do_servidor(cliente, agenda):
    ws = await _abrir(cliente)
    await ws.send_str(_consulta())
    await ws.send_str(_set(NIVEL_1, 44))
    await ws.send_str(_set(ONLINE_AV, 1, identificador=2))
    await ws.send_str(_set(COMANDO, "1:extra:" + TOKEN, identificador=3))
    await ws.send_str(_auth())
    await ws.send_str("nao e json")
    quadros = [json.dumps(q) for q in await _tudo(ws)]
    agenda.avancar(JANELA_A_S)
    await agenda.soltar(RELEITURA_S)
    quadros += [json.dumps(q) for q in await _tudo(ws)]
    # The api_token is the machine credential of the whole bus, and a frame that echoed
    # it would hand it to every other client of the same licence.
    assert len(quadros) >= 6
    assert not [quadro for quadro in quadros if TOKEN in quadro]
    await ws.close()


@pytest.mark.parametrize(
    "bruto",
    [
        "nao e json",
        "[]",
        "null",
        '"set"',
        "[" * 3000,
        '{"t":"nada"}',
        '{"t":"set","dpid":"121","v":1}',
        '{"t":"set","dpid":121.0,"v":1}',
        '{"t":"set","id":{"a":1},"dpid":121,"v":1}',
        '{"t":"consulta","id":"' + "x" * 65 + '"}',
        _auth(),
    ],
)
async def test_um_quadro_ruim_e_recusado_e_o_socket_segue_vivo(cliente, caixas, bruto):
    ws = await _abrir(cliente)
    await ws.send_str(bruto)
    assert _acks(await _tudo(ws)) == [
        {"t": "ack", "id": None, "ok": False, "code": "frame_invalido"}
    ]
    # The other end is whatever bridge somebody implemented from the public contract, and
    # one bad frame must not drop a socket that is carrying a whole licence; a second auth is
    # not a frame of the contract either, and it never re-authenticates a socket.
    assert _nada_chegou(caixas)
    await ws.send_str(_set(NIVEL_1, 30, identificador=9))
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 9, "ok": True, "code": None}]
    assert caixas.instancias[0].chamadas == [("volume", 30)]
    await ws.close()


async def test_uma_enxurrada_de_quadros_nao_cresce_nada_sem_limite(cliente):
    ws = await _abrir(cliente)
    quantos = 150
    for indice in range(quantos):
        await ws.send_str(_set(NIVEL_1, indice % 101, identificador=indice))
        await ws.send_str(_set(999, 1, identificador=indice))
    # Three hundred acks do not fit the drain window of _tudo on a loaded machine, and a
    # test that gives up early fails once in six runs for a reason that is not the product.
    quadros = await _ate_juntar(ws, quantos * 2)
    assert len(_acks(quadros)) == quantos * 2
    # One verification per data point and never one per frame, because a bridge in a loop
    # would otherwise spend a task of the daemon on every message it sends.
    assert _tarefas_do_barramento() == 1
    await ws.close()


async def test_um_cliente_que_cai_no_meio_da_verificacao_nao_deixa_nada(cliente, agenda):
    ws = await _abrir(cliente)
    await ws.send_str(_set(NIVEL_1, 44))
    await _tudo(ws)
    assert _tarefas_do_barramento() == 1
    assert cliente.app[BARRAMENTO].ouvintes_de(AV) == 1
    await ws.close()
    await agenda.girar()
    # A socket that went away and stayed in the set of listeners is a reference the
    # daemon of an appliance never gets back, and a bridge that reconnects on every hiccup
    # leaves one of them behind on every try.
    assert cliente.app[BARRAMENTO].ouvintes == 0
    agenda.avancar(JANELA_A_S)
    await agenda.soltar(RELEITURA_S)
    # A bridge that reconnects on every hiccup would leave one task per connection behind
    # it, and the daemon of an appliance never gets them back.
    assert _tarefas_do_barramento() == 0
    outro = await _abrir(cliente)
    await outro.send_str(_set(NIVEL_1, 45))
    assert [q for q in await _tudo(outro) if q["t"] == "report"]
    await outro.close()


async def test_um_quadro_maior_que_o_teto_nao_e_lido(cliente, caixas):
    ws = await _abrir(cliente)
    await ws.send_str(json.dumps({"t": "set", "id": 1, "dpid": COMANDO, "v": "x" * 9000}))
    # The reader holds a whole message in memory before anybody looks at it, so the
    # ceiling is what keeps a client from choosing how much of an ARM board it spends.
    assert (await ws.receive(timeout=2)).type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_QUADRO_GRANDE
    assert _nada_chegou(caixas)


async def test_a_consulta_traz_so_a_fatia_da_propria_licenca(cliente):
    ws = await _abrir(cliente, AV)
    outro = await _abrir(cliente, AR)
    await ws.send_str(_consulta())
    await outro.send_str(_consulta())
    (do_av,) = await _tudo(ws)
    (do_ar,) = await _tudo(outro)
    # Two licences are two devices on the platform, and the bridge of one must never
    # read the state of the other, not even the bit that says a machine is online.
    assert str(NIVEL_1) in do_av["dps"]
    assert str(ONLINE_AV) in do_av["dps"]
    assert str(ONLINE_AR) not in do_av["dps"]
    assert str(NOMES_MAQUINAS) not in do_av["dps"]
    assert str(ONLINE_AR) in do_ar["dps"]
    assert str(NIVEL_1) not in do_ar["dps"]
    assert str(ONLINE_AV) not in do_ar["dps"]
    assert str(GRUPO) not in do_ar["dps"]
    await ws.close()
    await outro.close()


def _acks(quadros: list[dict]) -> list[dict]:
    return [q for q in quadros if q.get("t") == "ack"]


def _tarefas_do_barramento() -> int:
    return len(
        [
            t
            for t in asyncio.all_tasks()
            if t.get_name().startswith("dpbus:verifica:") and not t.done()
        ]
    )


async def _ate_juntar(ws, acks: int, prazo_s: float = 5.0) -> list[dict]:
    """Every frame until that many acks arrived, or until the deadline gives up."""
    laco = asyncio.get_running_loop()
    limite = laco.time() + prazo_s
    quadros: list[dict] = []
    while len(_acks(quadros)) < acks and laco.time() < limite:
        try:
            mensagem = await ws.receive(timeout=0.2)
        except TimeoutError:
            continue
        if not isinstance(mensagem.data, str):
            break
        quadros.append(json.loads(mensagem.data))
    return quadros


async def _ate_fechar(ws, prazo_s: float = 2.0) -> None:
    """Waits for the socket to be gone, past whatever reports were already on the way.

    The bus publishes on its own, so asserting on the very next frame is a test that
    passes alone and fails in a full run. The close CODE is not asserted here on purpose: a
    socket closed from outside its handler task, which is parked reading it, reaches the peer
    as a dropped connection about as often as it reaches it as a code. What revocation has to
    guarantee is that the socket is gone and answers nothing, and that is what is asserted.
    """
    while True:
        mensagem = await ws.receive(timeout=prazo_s)
        if mensagem.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
            return


def _token_vivo(cliente) -> str:
    return cliente.app[SEGREDOS].valor.api_token


async def _nunca_mais_responde(ws) -> None:
    """A set on a closed socket is never acked, which is what "gone" has to mean.

    Writing may itself be refused by the closing transport, which is the same answer.
    """
    with contextlib.suppress(Exception):
        await ws.send_str(_set(NIVEL_1, 99))
    assert await _tudo(ws) == []


async def test_trocar_a_senha_fecha_o_socket_que_o_token_antigo_autenticou(
    cliente, posse, bearer, senha
):
    """Rotating the api_token has to end the sockets it authenticated.

    A socket authenticates on its FIRST frame and is never asked again, so without this
    the documented remediation for a leaked machine credential remediates nothing: whoever
    holds the old token keeps every number of every licence for as long as the daemon runs,
    and a bridge socket is long lived by design, so it never has to reconnect.
    """
    sessao = await posse(cliente)
    ws = await _abrir(cliente, AV, _token_vivo(cliente))
    outro = await _abrir(cliente, AR, _token_vivo(cliente))
    await ws.send_str(_consulta())
    await outro.send_str(_consulta())
    assert [q["t"] for q in await _tudo(ws)] == ["snapshot"]
    assert [q["t"] for q in await _tudo(outro)] == ["snapshot"]

    resposta = await cliente.post(
        "/api/senha",
        json={"senha_atual": senha, "senha_nova": "outra-senha-boa"},
        headers=bearer(sessao),
    )
    assert resposta.status == 200, await resposta.text()

    await _ate_fechar(ws)
    await _ate_fechar(outro)
    await _nunca_mais_responde(ws)
    await _nunca_mais_responde(outro)
    assert cliente.app[BARRAMENTO].ouvintes == 0


async def test_tomar_posse_fecha_o_socket_que_o_token_antigo_autenticou(cliente, posse):
    """The same rotation happens when a hub with an erased config.json is claimed again."""
    ws = await _abrir(cliente)
    await ws.send_str(_consulta())
    assert [q["t"] for q in await _tudo(ws)] == ["snapshot"]

    await posse(cliente)

    await _ate_fechar(ws)
    await _nunca_mais_responde(ws)
    assert cliente.app[BARRAMENTO].ouvintes == 0
    # The old token is dead with the sockets, so a bridge that kept it cannot come back.
    outro = await cliente.ws_connect(CAMINHO)
    await outro.send_str(_auth(TOKEN))
    await _fechado_com_4401(outro)


async def test_uma_licenca_removida_nao_responde_mais_nem_abre_socket_novo(
    cliente, posse, bearer, ares
):
    """Erasing a licence empties its numbers, and the bridge of that device loses
    the socket it held and cannot open another one, while the other licence goes on.
    """
    sessao = await posse(cliente)
    token = _token_vivo(cliente)
    do_ar = await _abrir(cliente, AR, token)
    do_av = await _abrir(cliente, AV, token)
    await do_ar.send_str(_consulta())
    await do_av.send_str(_consulta())
    assert [q["t"] for q in await _tudo(do_ar)] == ["snapshot"]
    assert [q["t"] for q in await _tudo(do_av)] == ["snapshot"]

    resposta = await cliente.delete(f"/api/licencas/{AR}", headers=bearer(sessao))
    assert resposta.status == 200, await resposta.text()

    await _ate_fechar(do_ar)
    await _nunca_mais_responde(do_ar)
    assert cliente.app[BARRAMENTO].ouvintes_de(AR) == 0
    outro = await cliente.ws_connect(CAMINHO)
    await outro.send_str(_auth(token, AR))
    await outro.send_str(_set(101, True))
    await _fechado_com_4401(outro)
    assert _nada_chegou(ares)
    # The other licence never noticed.
    await do_av.send_str(_set(NIVEL_1, 30, identificador=2))
    assert _acks(await _tudo(do_av)) == [{"t": "ack", "id": 2, "ok": True, "code": None}]
    await do_av.close()
