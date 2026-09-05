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

Seções 8 e 9 sob ataque no socket do barramento: toda regra aqui é uma tentativa de quebrá-lo,
nunca um caminho feliz.

O barramento é a porta da ponte para a instalação inteira, ele não leva sessão e fica exposto
na LAN do cliente sem TLS, então o que ele recusa é o que protege todo número de toda licença.
Atacados aqui: um quadro que chega antes do auth, um token contrabandeado na URL, um token
errado, um token fora do ASCII, uma licença que este hub não tem, silêncio depois do prazo,
um set num data point que ninguém pode ajustar, um set num data point do OUTRO produto, um set
num número que ninguém ocupa, um canal de comando alimentado com caracteres de controle e uma
página de texto, uma página de outro site abrindo o socket, um Host que não é este hub, o
api_token vazando de volta num quadro, uma enxurrada de quadros, um quadro grande demais, um
cliente que some no meio de uma verificação e uma licença que saiu da instalação.
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

# The numbers of section 8, written by hand on purpose.
# Os números da seção 8, escritos na mão de propósito.
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
    """A speaker that writes down every command, so a test proves nothing reached it.

    Uma caixa que anota todo comando, para um teste provar que nada chegou nela.
    """

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
    """An air conditioner that writes down every command, for the same proof.

    Um ar condicionado que anota todo comando, para a mesma prova.
    """

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

    Uma caixa no número 1 de uma licença de áudio e vídeo, números 2 a 12 vazios, um ar
    condicionado no número 1 de uma licença de ar, e um relógio que o teste move na mão.
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
    # Why: section 8, the FIRST frame authenticates; a bus that ran a set before it would hand
    # the volume of the whole house to anybody who reached the port.
    # Por que: seção 8, o PRIMEIRO quadro autentica; um barramento que rodasse um set antes
    # entregaria o volume da casa inteira a quem alcançasse a porta.
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas)


async def test_uma_consulta_antes_do_auth_nao_e_respondida(cliente):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_consulta())
    # Why: the snapshot is the state of every number of a licence, and a socket that has not
    # said who it is gets nothing, not even a read.
    # Por que: o snapshot é o estado de todo número de uma licença, e um socket que não disse
    # quem é não recebe nada, nem uma leitura.
    await _fechado_com_4401(ws)


async def test_um_token_na_url_nao_autentica(cliente, caixas, agenda):
    # Why: a query string is written into every access log and into the history of whoever
    # pasted it, so the token of section 9 only ever travels inside the first frame.
    # Por que: uma query string é escrita em todo log de acesso e no histórico de quem a colou,
    # então o token da seção 9 só viaja dentro do primeiro quadro.
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
    # Why: section 8, the first frame names the token AND the licence; half of it names
    # nothing, and a bus that guessed the missing half would hand out a licence by luck.
    # Por que: seção 8, o primeiro quadro nomeia o token E a licença; metade dele não nomeia
    # nada, e um barramento que adivinhasse a metade que falta entregaria uma licença na sorte.
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas)


@pytest.mark.parametrize("token", ["tökén-de-maquina", "\ud800", "senha ção"])
async def test_um_token_fora_do_ascii_fecha_4401_em_vez_de_estourar(cliente, token):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(token))
    # Why: the api_token is a token_urlsafe, which is ASCII, and comparing a non ASCII string
    # in constant time raises instead of answering that it does not match; a raise here would
    # be a traceback in the log for every probe and a socket that closes with no code.
    # Por que: o api_token é um token_urlsafe, que é ASCII, e comparar uma string não ASCII em
    # tempo constante estoura em vez de responder que não casa; um estouro aqui seria um
    # traceback no log a cada sonda e um socket que fecha sem código.
    await _fechado_com_4401(ws)
    outro = await _abrir(cliente)
    await outro.send_str(_consulta())
    assert [q["t"] for q in await _tudo(outro)] == ["snapshot"]
    await outro.close()


async def test_o_token_e_comparado_em_tempo_constante(cliente, monkeypatch):
    """The shape of the comparison is what a test can assert: the token of the frame reaches
    the constant time primitive of the standard library, whole, against the token of the hub.

    Why: comparing with == hands whoever measures the answer the length of the common prefix,
    and this token is the machine credential of the whole bus; timing cannot be measured in a
    test, so the test proves the primitive is the one that judges.

    A forma da comparação é o que um teste pode afirmar: o token do quadro chega inteiro à
    primitiva de tempo constante da biblioteca padrão, contra o token do hub.

    Por que: comparar com == entrega a quem mede a resposta o tamanho do prefixo comum, e este
    token é a credencial de máquina do barramento inteiro; tempo não se mede num teste, então
    o teste prova que a primitiva é quem julga.
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
    # Why: the right token with the wrong licence is a bridge of another hub, or a probe that
    # knows the token and is looking for what this hub has; the answer is the close and no
    # frame, so the probe does not even learn which licences exist.
    # Por que: o token certo com a licença errada é uma ponte de outro hub, ou uma sonda que
    # sabe o token e procura o que este hub tem; a resposta é o fechamento e quadro nenhum,
    # então a sonda nem aprende quais licenças existem.
    await _fechado_com_4401(ws)
    assert _nada_chegou(caixas, ares)
    assert cliente.app[BARRAMENTO].ouvintes == 0


async def test_sem_auth_em_cinco_segundos_fecha_4401(cliente, agenda):
    ws = await cliente.ws_connect(CAMINHO)
    await agenda.girar()
    # Why: a socket that says nothing holds a connection of the daemon for free, and the
    # deadline of section 8 is what takes it back.
    # Por que: um socket que não fala segura uma conexão do daemon de graça, e o prazo da
    # seção 8 é o que a retoma.
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
    # Why: section 8, the chip never echoes and a report is only ever born of real state; a
    # set accepted here would publish a speaker as online because somebody asked for it.
    # Por que: seção 8, o chip nunca ecoa e um report só nasce de estado real; um set aceito
    # aqui publicaria uma caixa como online porque alguém pediu.
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
    # Why: section 8, a licence is a device of ONE product and its socket speaks the table of
    # that product only; a number the other table does not have (the fifth of every machine
    # of air is free, and nothing of air lives past 140) is refused as unknown, so a set
    # never lands on a number of a licence the bridge did not name.
    # Por que: seção 8, uma licença é um dispositivo de UM produto e o socket dela fala só a
    # tabela daquele produto; um número que a outra tabela não tem (o quinto de toda máquina
    # de ar fica livre, e nada de ar vive depois do 140) é recusado como desconhecido, então
    # um set nunca cai num número de uma licença que a ponte não nomeou.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "dp_desconhecido"}]
    assert _nada_chegou(caixas, ares)
    await ws.close()


async def test_um_dpid_e_lido_contra_a_tabela_do_produto_da_licenca(cliente, caixas, ares):
    # Why: 102 is the power of equipment 2 on audio and video and the setpoint of machine 1
    # on air, so the same frame is judged by what it means on the licence that received it;
    # a bus that read it by the other table would switch a machine with a temperature.
    # Por que: 102 é o ligado do equipamento 2 em áudio e vídeo e o setpoint da máquina 1 em
    # ar, então o mesmo quadro é julgado pelo que significa na licença que o recebeu; um
    # barramento que o lesse pela outra tabela ligaria uma máquina com uma temperatura.
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
    # Why: an empty number reaches no equipment, and answering ok for it would tell the bridge
    # that a number nobody registered took the command.
    # Por que: um número vazio não alcança equipamento nenhum, e responder ok por ele diria à
    # ponte que um número que ninguém cadastrou aceitou o comando.
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
    # Why: the command channel is the one string a bridge writes, and what the driver puts on
    # the wire of the device comes from it; a control character or a page of text is refused
    # by the contract before any parser, any lock and any driver sees it.
    # Por que: o canal de comando é a única string que uma ponte escreve, e o que o driver põe
    # no fio do aparelho vem dela; um caractere de controle ou uma página de texto é recusado
    # pelo contrato antes de qualquer analisador, qualquer trava e qualquer driver ver.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "valor_invalido"}]
    assert _nada_chegou(caixas)
    await ws.close()


async def test_um_origin_de_outro_site_nao_abre_o_socket(cliente, caixas):
    # Why: section 9, a page of another site loaded by whoever is on the network would open
    # this socket with the browser of the integrator if the Origin rule did not stop it.
    # Por que: seção 9, uma página de outro site aberta por quem está na rede abriria este
    # socket com o navegador do integrador se a regra de Origin não a barrasse.
    with pytest.raises(WSServerHandshakeError) as erro:
        await cliente.ws_connect(CAMINHO, headers={"Origin": ALHEIA})
    assert erro.value.status == 403
    for nome, valor in CABECALHOS.items():
        assert erro.value.headers.get(nome) == valor, nome
    assert _nada_chegou(caixas)


async def test_um_host_fora_da_lista_nao_abre_o_socket(cliente):
    # Why: section 9, the Host rule is what closes DNS rebinding without the attacker being on
    # the LAN, and the bus is not outside it just because it is not under /api/.
    # Por que: seção 9, a regra de Host é o que fecha DNS rebinding sem o atacante estar na
    # LAN, e o barramento não está fora dela só por não ficar sob /api/.
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
    # Why: the api_token is the machine credential of the whole bus, and a frame that echoed
    # it would hand it to every other client of the same licence.
    # Por que: o api_token é a credencial de máquina do barramento inteiro, e um quadro que o
    # ecoasse o entregaria a todo outro cliente da mesma licença.
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
    # Why: the other end is whatever bridge somebody implemented from the public contract, and
    # one bad frame must not drop a socket that is carrying a whole licence; a second auth is
    # not a frame of the contract either, and it never re-authenticates a socket.
    # Por que: do outro lado está a ponte que alguém implementou do contrato público, e um
    # quadro ruim não pode derrubar um socket que carrega uma licença inteira; um segundo auth
    # também não é quadro do contrato, e nunca reautentica um socket.
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
    # Why: three hundred acks do not fit the drain window of _tudo on a loaded machine, and a
    # test that gives up early fails once in six runs for a reason that is not the product.
    # Por que: trezentos acks não cabem na janela de _tudo numa máquina cheia, e um teste que
    # desiste cedo falha uma vez em seis por um motivo que não é o produto.
    quadros = await _ate_juntar(ws, quantos * 2)
    assert len(_acks(quadros)) == quantos * 2
    # Why: one verification per data point and never one per frame, because a bridge in a loop
    # would otherwise spend a task of the daemon on every message it sends.
    # Por que: uma verificação por data point e nunca uma por quadro, porque uma ponte em laço
    # gastaria uma tarefa do daemon a cada mensagem que mandasse.
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
    # Why: a socket that went away and stayed in the set of listeners is a reference the
    # daemon of an appliance never gets back, and a bridge that reconnects on every hiccup
    # leaves one of them behind on every try.
    # Por que: um socket que foi embora e ficou no conjunto de ouvintes é uma referência que o
    # daemon de um appliance nunca recupera, e uma ponte que reconecta a cada soluço deixa uma
    # dessas para trás a cada tentativa.
    assert cliente.app[BARRAMENTO].ouvintes == 0
    agenda.avancar(JANELA_A_S)
    await agenda.soltar(RELEITURA_S)
    # Why: a bridge that reconnects on every hiccup would leave one task per connection behind
    # it, and the daemon of an appliance never gets them back.
    # Por que: uma ponte que reconecta a cada soluço deixaria uma tarefa por conexão para trás,
    # e o daemon de um appliance nunca as recupera.
    assert _tarefas_do_barramento() == 0
    outro = await _abrir(cliente)
    await outro.send_str(_set(NIVEL_1, 45))
    assert [q for q in await _tudo(outro) if q["t"] == "report"]
    await outro.close()


async def test_um_quadro_maior_que_o_teto_nao_e_lido(cliente, caixas):
    ws = await _abrir(cliente)
    await ws.send_str(json.dumps({"t": "set", "id": 1, "dpid": COMANDO, "v": "x" * 9000}))
    # Why: the reader holds a whole message in memory before anybody looks at it, so the
    # ceiling is what keeps a client from choosing how much of an ARM board it spends.
    # Por que: o leitor guarda a mensagem inteira na memória antes de alguém olhar, então o
    # teto é o que impede um cliente de escolher quanto de uma placa ARM ele gasta.
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
    # Why: two licences are two devices on the platform, and the bridge of one must never
    # read the state of the other, not even the bit that says a machine is online.
    # Por que: duas licenças são dois dispositivos na plataforma, e a ponte de uma nunca pode
    # ler o estado da outra, nem o bit que diz que uma máquina está online.
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
    """Every frame until that many acks arrived, or until the deadline gives up.

    Cada quadro até chegarem tantos acks, ou até o prazo desistir.
    """
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

    Why: the bus publishes on its own, so asserting on the very next frame is a test that
    passes alone and fails in a full run. The close CODE is not asserted here on purpose: a
    socket closed from outside its handler task, which is parked reading it, reaches the peer
    as a dropped connection about as often as it reaches it as a code. What revocation has to
    guarantee is that the socket is gone and answers nothing, and that is what is asserted.

    Espera o socket ter acabado, passando os reports que já estavam a caminho.

    Por que: o barramento publica por conta própria, então afirmar sobre o quadro seguinte é
    um teste que passa sozinho e falha na rodada inteira. O CÓDIGO de fechamento não é
    afirmado aqui de propósito: um socket fechado de fora da tarefa do handler, que está
    parada lendo, chega ao outro lado como conexão caída quase tanto quanto chega como
    código. O que a revogação precisa garantir é que o socket acabou e não responde mais, e é
    isso que se afirma.
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

    Um set num socket fechado nunca é confirmado, que é o que "acabou" precisa significar.

    Escrever pode ser recusado pelo próprio transporte fechando, que é a mesma resposta.
    """
    with contextlib.suppress(Exception):
        await ws.send_str(_set(NIVEL_1, 99))
    assert await _tudo(ws) == []


async def test_trocar_a_senha_fecha_o_socket_que_o_token_antigo_autenticou(
    cliente, posse, bearer, senha
):
    """Section 9: rotating the api_token has to end the sockets it authenticated.

    Why: a socket authenticates on its FIRST frame and is never asked again, so without this
    the documented remediation for a leaked machine credential remediates nothing: whoever
    holds the old token keeps every number of every licence for as long as the daemon runs,
    and a bridge socket is long lived by design, so it never has to reconnect.

    Seção 9: rotacionar o api_token precisa encerrar os sockets que ele autenticou.

    Por que: um socket autentica no PRIMEIRO quadro e nunca mais é perguntado, então sem isto
    a remediação documentada de uma credencial de máquina vazada não remedia nada: quem tem o
    token antigo mantém todo número de toda licença enquanto o daemon viver, e um socket de
    ponte é longevo por projeto, então ele nunca reconecta.
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
    """The same rotation happens when a hub with an erased config.json is claimed again.

    A mesma rotação acontece quando um hub com o config.json apagado é reivindicado de novo.
    """
    ws = await _abrir(cliente)
    await ws.send_str(_consulta())
    assert [q["t"] for q in await _tudo(ws)] == ["snapshot"]

    await posse(cliente)

    await _ate_fechar(ws)
    await _nunca_mais_responde(ws)
    assert cliente.app[BARRAMENTO].ouvintes == 0
    # Why: the old token is dead with the sockets, so a bridge that kept it cannot come back.
    # Por que: o token antigo morreu com os sockets, então uma ponte que o guardou não volta.
    outro = await cliente.ws_connect(CAMINHO)
    await outro.send_str(_auth(TOKEN))
    await _fechado_com_4401(outro)


async def test_uma_licenca_removida_nao_responde_mais_nem_abre_socket_novo(
    cliente, posse, bearer, ares
):
    """Section 9: erasing a licence empties its numbers, and the bridge of that device loses
    the socket it held and cannot open another one, while the other licence goes on.

    Seção 9: apagar uma licença esvazia os números dela, e a ponte daquele dispositivo perde
    o socket que tinha e não abre outro, enquanto a outra licença segue.
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
    # A outra licença nem percebeu.
    await do_av.send_str(_set(NIVEL_1, 30, identificador=2))
    assert _acks(await _tudo(do_av)) == [{"t": "ack", "id": 2, "ok": True, "code": None}]
    await do_av.close()
