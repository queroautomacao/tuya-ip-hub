# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 8 and 9 under attack on the socket of the bus: every rule here is an attempt to
break it, never a happy path.

The bus is the door of the bridge into the whole installation, it carries no session and it
is exposed on the LAN of the customer with no TLS, so what it refuses is what protects six
blocks. Attacked here: a frame that arrives before the auth, a token smuggled in the URL, a
wrong token, silence past the deadline, a set on a data point nobody may set, a set on a block
nobody occupies, a page of another site opening the socket, a Host that is not this hub, the
api_token leaking back into a frame, a flood of frames, an oversized frame and a client that
disappears in the middle of a verification.

Seções 8 e 9 sob ataque no socket do barramento: toda regra aqui é uma tentativa de quebrá-lo,
nunca um caminho feliz.

O barramento é a porta da ponte para a instalação inteira, ele não leva sessão e fica exposto
na LAN do cliente sem TLS, então o que ele recusa é o que protege seis blocos. Atacados aqui:
um quadro que chega antes do auth, um token contrabandeado na URL, um token errado, silêncio
depois do prazo, um set num data point que ninguém pode ajustar, um set num bloco que ninguém
ocupa, uma página de outro site abrindo o socket, um Host que não é este hub, o api_token
vazando de volta num quadro, uma enxurrada de quadros, um quadro grande demais e um cliente
que some no meio de uma verificação.
"""

import asyncio
import base64
import contextlib
import json
import os

import pytest
from aiohttp import WSMsgType, WSServerHandshakeError

from iphub.api.comum import SEGREDOS
from iphub.config import Cadastro, Config
from iphub.dpbus.socket import BARRAMENTO
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto
from iphub.portao import CABECALHOS, SERVIDOR
from iphub.segredos import Segredos

CAMINHO = "/dpbus"
TIPO = "multiroom_falso"
TOKEN = "token-de-maquina-so-deste-teste"
IP_1 = "192.0.2.11"
ALHEIA = "http://evil.example.com"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")

# The numbers of section 8, written by hand on purpose.
# Os números da seção 8, escritos na mão de propósito.
VOLUME_1, ONLINE_1, TOCANDO_1 = 101, 104, 105
VOLUME_3 = 111
NOMES_BLOCOS, NOMES_CENAS, NOMES_GRUPOS = 133, 134, 135

FECHAMENTO_NAO_AUTENTICADO = 4401
FECHAMENTO_QUADRO_GRANDE = 1009
PRAZO_AUTH_S = 5.0
RELEITURA_S = 1.5


def _manifesto() -> Manifesto:
    textos = {"descricao": "Caixa de teste"}
    return Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Caixa", "en": "Speaker"},
        categoria="multiroom",
        capacidades=CAPACIDADES,
        textos={"pt": dict(textos), "en": dict(textos)},
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
            self._defina(online=True, volume=20, fonte="wifi", fontes=("wifi",), tocando=None)
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

        escravo_alheio = False

        def e_escravo(self) -> bool:

            return self.escravo_alheio

        def saiu_do_grupo(self) -> bool:
            return False

    return Falsa


@pytest.fixture
def caixas() -> type[Driver]:
    return _fabrica()


@pytest.fixture
async def cliente(fabrica_cliente, agenda, caixas):
    """One speaker in block 1, blocks 2 to 6 empty, and a clock the test moves by hand.

    Uma caixa no bloco 1, blocos 2 a 6 vazios, e um relógio que o teste move na mão.
    """
    return await fabrica_cliente(
        config=Config(
            equipamentos=(Cadastro(identidade="uuid-1", tipo=TIPO, nome="Sala", ip=IP_1),),
            blocos=("uuid-1",),
        ),
        segredos=Segredos(api_token=TOKEN),
        catalogo={TIPO: caixas},
        dormir=agenda.dormir,
        agora=agenda,
    )


def _set(dpid: int, valor: object, identificador: int = 1) -> str:
    return json.dumps({"t": "set", "id": identificador, "dpid": dpid, "v": valor})


def _auth(token: str = TOKEN) -> str:
    return json.dumps({"t": "auth", "token": token})


async def _abrir(cliente, **extras):
    ws = await cliente.ws_connect(CAMINHO, **extras)
    await ws.send_str(_auth())
    await ws.receive(timeout=2)
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


async def test_nenhum_quadro_antes_do_auth_e_honrado(cliente, caixas):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_set(VOLUME_1, 99))
    mensagem = await ws.receive(timeout=2)
    # Why: section 8, the FIRST frame authenticates; a bus that ran a set before it would hand
    # the volume of the whole house to anybody who reached the port.
    # Por que: seção 8, o PRIMEIRO quadro autentica; um barramento que rodasse um set antes
    # entregaria o volume da casa inteira a quem alcançasse a porta.
    assert mensagem.type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_NAO_AUTENTICADO
    assert caixas.instancias[0].chamadas == []


async def test_um_token_na_url_nao_autentica(cliente, caixas, agenda):
    # Why: a query string is written into every access log and into the history of whoever
    # pasted it, so the token of section 9 only ever travels inside the first frame.
    # Por que: uma query string é escrita em todo log de acesso e no histórico de quem a colou,
    # então o token da seção 9 só viaja dentro do primeiro quadro.
    ws = await cliente.ws_connect(f"{CAMINHO}?token={TOKEN}")
    await ws.send_str(_set(VOLUME_1, 99))
    assert (await ws.receive(timeout=2)).type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_NAO_AUTENTICADO
    assert caixas.instancias[0].chamadas == []
    outro = await cliente.ws_connect(f"{CAMINHO}?token={TOKEN}")
    assert await agenda.soltar(PRAZO_AUTH_S) == 1
    assert (await outro.receive(timeout=2)).type is WSMsgType.CLOSE
    assert outro.close_code == FECHAMENTO_NAO_AUTENTICADO


@pytest.mark.parametrize(
    "token", ["", "outro-token", TOKEN + "x", TOKEN[:-1], TOKEN.upper(), "x" * 4000]
)
async def test_um_token_que_nao_casa_fecha_4401(cliente, token):
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(token))
    assert (await ws.receive(timeout=2)).type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_NAO_AUTENTICADO


async def test_sem_auth_em_cinco_segundos_fecha_4401(cliente, agenda):
    ws = await cliente.ws_connect(CAMINHO)
    await agenda.girar()
    # Why: a socket that says nothing holds a connection of the daemon for free, and the
    # deadline of section 8 is what takes it back.
    # Por que: um socket que não fala segura uma conexão do daemon de graça, e o prazo da
    # seção 8 é o que a retoma.
    assert agenda.presas(PRAZO_AUTH_S) == 1
    assert await agenda.soltar(PRAZO_AUTH_S) == 1
    assert (await ws.receive(timeout=2)).type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_NAO_AUTENTICADO


async def test_um_auth_certo_depois_do_prazo_nao_salva_o_socket(cliente, agenda):
    ws = await cliente.ws_connect(CAMINHO)
    await agenda.girar()
    await agenda.soltar(PRAZO_AUTH_S)
    await ws.send_str(_auth())
    assert (await ws.receive(timeout=2)).type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_NAO_AUTENTICADO


@pytest.mark.parametrize(
    ("dpid", "valor"),
    [
        (ONLINE_1, True),
        (TOCANDO_1, "Musica"),
        (NOMES_BLOCOS, '{"z":[]}'),
        (NOMES_CENAS, '{"c":[]}'),
        (NOMES_GRUPOS, '{"g":[]}'),
    ],
)
async def test_um_set_num_dp_de_so_report_e_recusado(cliente, caixas, dpid, valor):
    ws = await _abrir(cliente)
    await ws.send_str(_set(dpid, valor))
    # Why: section 8, the chip never echoes and a report is only ever born of real state; a
    # set accepted here would publish a speaker as online because somebody asked for it.
    # Por que: seção 8, o chip nunca ecoa e um report só nasce de estado real; um set aceito
    # aqui publicaria uma caixa como online porque alguém pediu.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "dp_somente_leitura"}]
    assert caixas.instancias[0].chamadas == []


async def test_um_set_numa_bloco_que_ninguem_ocupa_e_recusado(cliente, caixas):
    ws = await _abrir(cliente)
    await ws.send_str(_set(VOLUME_3, 50))
    # Why: an empty block reaches no equipment, and answering ok for it would tell the bridge
    # that a block nobody registered took the command.
    # Por que: um bloco vazio não alcança equipamento nenhum, e responder ok por ele diria à
    # ponte que um bloco que ninguém cadastrou aceitou o comando.
    assert await _tudo(ws) == [{"t": "ack", "id": 1, "ok": False, "code": "bloco_offline"}]
    assert caixas.instancias[0].chamadas == []


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
    assert caixas.instancias[0].chamadas == []


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
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth())
    quadros = [(await ws.receive(timeout=2)).data]
    await ws.send_str(_set(VOLUME_1, 44))
    await ws.send_str(_set(ONLINE_1, True, identificador=2))
    await ws.send_str("nao e json")
    quadros += [json.dumps(q) for q in await _tudo(ws)]
    await agenda.soltar(RELEITURA_S)
    quadros += [json.dumps(q) for q in await _tudo(ws)]
    # Why: the api_token is the machine credential of the whole bus, and a frame that echoed
    # it would hand it to every other client of the same broadcast.
    # Por que: o api_token é a credencial de máquina do barramento inteiro, e um quadro que o
    # ecoasse o entregaria a todo outro cliente da mesma difusão.
    assert quadros
    assert not [quadro for quadro in quadros if TOKEN in quadro]


async def test_uma_enxurrada_de_quadros_nao_cresce_nada_sem_limite(cliente):
    ws = await _abrir(cliente)
    quantos = 150
    for indice in range(quantos):
        await ws.send_str(_set(VOLUME_1, indice % 101, identificador=indice))
        await ws.send_str(_set(999, 1, identificador=indice))
    # Why: three hundred acks do not fit the drain window of _tudo on a loaded machine, and a
    # test that gives up early fails once in six runs for a reason that is not the product.
    # Por que: trezentos acks não cabem na janela de _tudo numa máquina cheia, e um teste que
    # desiste cedo falha uma vez em seis por um motivo que não é o produto.
    quadros = await _ate_juntar(ws, quantos * 2)
    assert len([q for q in quadros if q["t"] == "ack"]) == quantos * 2
    # Why: one verification per data point and never one per frame, because a bridge in a loop
    # would otherwise spend a task of the daemon on every message it sends.
    # Por que: uma verificação por data point e nunca uma por quadro, porque uma ponte em laço
    # gastaria uma tarefa do daemon a cada mensagem que mandasse.
    assert _tarefas_do_barramento() == 1


async def test_um_cliente_que_cai_no_meio_da_verificacao_nao_deixa_nada(cliente, agenda):
    ws = await _abrir(cliente)
    await ws.send_str(_set(VOLUME_1, 44))
    await _tudo(ws)
    assert _tarefas_do_barramento() == 1
    assert cliente.app[BARRAMENTO].ouvintes == 1
    await ws.close()
    await agenda.girar()
    # Why: a socket that went away and stayed in the set of listeners is a reference the
    # daemon of an appliance never gets back, and a bridge that reconnects on every hiccup
    # leaves one of them behind on every try.
    # Por que: um socket que foi embora e ficou no conjunto de ouvintes é uma referência que o
    # daemon de um appliance nunca recupera, e uma ponte que reconecta a cada soluço deixa uma
    # dessas para trás a cada tentativa.
    assert cliente.app[BARRAMENTO].ouvintes == 0
    await agenda.soltar(RELEITURA_S)
    # Why: a bridge that reconnects on every hiccup would leave one task per connection behind
    # it, and the daemon of an appliance never gets them back.
    # Por que: uma ponte que reconecta a cada soluço deixaria uma tarefa por conexão para trás,
    # e o daemon de um appliance nunca as recupera.
    assert _tarefas_do_barramento() == 0
    outro = await _abrir(cliente)
    await outro.send_str(_set(VOLUME_1, 45))
    assert [q for q in await _tudo(outro) if q["t"] == "report"]


async def test_um_quadro_maior_que_o_teto_nao_e_lido(cliente, caixas):
    ws = await _abrir(cliente)
    await ws.send_str(json.dumps({"t": "set", "id": 1, "dpid": VOLUME_1, "v": "x" * 9000}))
    # Why: the reader holds a whole message in memory before anybody looks at it, so the
    # ceiling is what keeps a client from choosing how much of an ARM board it spends.
    # Por que: o leitor guarda a mensagem inteira na memória antes de alguém olhar, então o
    # teto é o que impede um cliente de escolher quanto de uma placa ARM ele gasta.
    assert (await ws.receive(timeout=2)).type is WSMsgType.CLOSE
    assert ws.close_code == FECHAMENTO_QUADRO_GRANDE
    assert caixas.instancias[0].chamadas == []


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
    while len([q for q in quadros if q["t"] == "ack"]) < acks and laco.time() < limite:
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


async def test_trocar_a_senha_fecha_o_socket_que_o_token_antigo_autenticou(
    cliente, posse, bearer, senha
):
    """Section 9: rotating the api_token has to end the sockets it authenticated.

    Why: a socket authenticates on its FIRST frame and is never asked again, so without this
    the documented remediation for a leaked machine credential remediates nothing: whoever
    holds the old token keeps volume, transport, input, group and scene control of every block
    for as long as the daemon runs, and a bridge socket is long lived by design, so it never
    has to reconnect.

    Seção 9: rotacionar o api_token precisa encerrar os sockets que ele autenticou.

    Por que: um socket autentica no PRIMEIRO quadro e nunca mais é perguntado, então sem isto
    a remediação documentada de uma credencial de máquina vazada não remedia nada: quem tem o
    token antigo mantém volume, transporte, entrada, grupo e cena de todo bloco enquanto o
    daemon viver, e um socket de ponte é longevo por projeto, então ele nunca reconecta.
    """
    sessao = await posse(cliente)
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(_token_vivo(cliente)))
    await ws.receive(timeout=2)
    assert not ws.closed

    resposta = await cliente.post(
        "/api/senha",
        json={"senha_atual": senha, "senha_nova": "outra-senha-boa"},
        headers=bearer(sessao),
    )
    assert resposta.status == 200, await resposta.text()

    await _ate_fechar(ws)
    # A set on the closed socket is never acked, which is what "revoked" has to mean.
    # Um set no socket fechado nunca é confirmado, que é o que "revogado" precisa significar.
    # Writing may itself be refused by the closing transport, which is the same answer.
    # Escrever pode ser recusado pelo próprio transporte fechando, que é a mesma resposta.
    with contextlib.suppress(Exception):
        await ws.send_str(_set(VOLUME_1, 99))
    assert await _tudo(ws) == []
    assert cliente.app[BARRAMENTO].ouvintes == 0


async def test_tomar_posse_fecha_o_socket_que_o_token_antigo_autenticou(cliente, posse):
    """The same rotation happens when a hub with an erased config.json is claimed again.

    A mesma rotação acontece quando um hub com o config.json apagado é reivindicado de novo.
    """
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(TOKEN))
    await ws.receive(timeout=2)
    assert not ws.closed

    await posse(cliente)

    await _ate_fechar(ws)
    assert cliente.app[BARRAMENTO].ouvintes == 0


async def test_um_cliente_que_para_de_ler_nao_congela_o_barramento(cliente):
    """A bridge that stops reading must not freeze the single task that publishes every
    report and reconciles the group for all six blocks.

    Why: send_str waits for the kernel buffer, so one stalled socket held the publish loop of
    the whole hub for everybody, and the frames it never took grew without bound in the daemon
    of an appliance. A socket that does not take a frame within the deadline is dropped.

    Uma ponte que para de ler não pode congelar a única tarefa que publica todo report e
    reconcilia o grupo dos seis blocos.

    Por que: o send_str espera pelo buffer do kernel, então um socket travado segurava o laço
    de publicação do hub inteiro para todo mundo, e os quadros que ele nunca pegou cresciam
    sem limite no daemon de um appliance. Um socket que não pega um quadro dentro do prazo é
    descartado.
    """
    barramento = cliente.app[BARRAMENTO]
    barramento._envio_s = 0.2
    travado = _Travado()
    barramento._clientes.add(travado)
    laco = asyncio.get_running_loop()
    comeco = laco.time()
    await barramento.publicar()
    gasto = laco.time() - comeco
    # Bounded by the deadline and a little slack, and not by the client, which never reads.
    # Limitado pelo prazo e uma folga, e não pelo cliente, que nunca lê.
    assert gasto < 2.0, f"the publish loop waited {gasto:.1f}s on one client that stopped reading"
    assert travado not in barramento._clientes
    assert travado.fechado


class _Travado:
    """A socket that accepted the connection and never reads another byte.

    Um socket que aceitou a conexão e nunca mais lê um byte.
    """

    def __init__(self) -> None:
        self.fechado = False

    async def send_str(self, _texto: str) -> None:
        await asyncio.Event().wait()

    async def close(self, code: int | None = None) -> None:
        self.fechado = True
