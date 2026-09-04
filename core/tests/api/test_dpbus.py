# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the DP-bus over a real socket: the first frame, the snapshot, the acks and
the optimistic report with its reread.

Everything here talks to the daemon the way the Tuya bridge will: a WebSocket, JSON frames
and the api_token of section 9 on the FIRST frame. The speakers are fakes of the driver
contract of section 6, never a real one, and the two waits of the bus (the five seconds of
the auth and the second and a half of the reread) are moved by hand, so this file is fast and
deterministic and no test sleeps.

The data point numbers are written by hand here. A test that asked the map for them would
agree with any change the map made to the contract of section 8, which is exactly what a
contract test exists to catch.

O contrato do DP-bus sobre um socket de verdade: o primeiro quadro, o snapshot, os acks e o
report otimista com a releitura dele.

Tudo aqui fala com o daemon do jeito que a ponte Tuya vai falar: um WebSocket, quadros JSON e
o api_token da seção 9 no PRIMEIRO quadro. As caixas são falsas do contrato de driver da
seção 6, nunca uma de verdade, e as duas esperas do barramento (os cinco segundos do auth e o
segundo e meio da releitura) são movidas na mão, então este arquivo é rápido e determinístico
e nenhum teste dorme.

Os números de data point são escritos na mão aqui. Um teste que os pedisse ao mapa concordaria
com qualquer mudança que o mapa fizesse no contrato da seção 8, que é exatamente o que um
teste de contrato existe para pegar.
"""

import asyncio
import json

import pytest

from iphub.api.comum import BLOCOS
from iphub.cenas import Cena, Passo
from iphub.config import Cadastro, Config
from iphub.dpbus.socket import BARRAMENTO, Barramento
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto
from iphub.segredos import Segredos

CAMINHO = "/dpbus"
TIPO = "multiroom_falso"
TOKEN = "token-de-maquina-so-deste-teste"
IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"
MUSICA = "Musica 1 - Artista"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")
FONTES = ("wifi", "line-in")

# The numbers of section 8, written by hand on purpose.
# Os números da seção 8, escritos na mão de propósito.
VOLUME_1, PRESET_1, ONLINE_1, TOCANDO_1, ENTRADA_1 = 101, 103, 104, 105, 141
VOLUME_2 = 106
CENA, GRUPO, NOMES_BLOCOS, NOMES_CENAS = 131, 132, 133, 134

# The two waits of the bus, which the fake clock releases by name.
# As duas esperas do barramento, que o relógio falso solta pelo nome.
RELEITURA_S = 1.5
INTERVALO_S = 1.0


class _Grupo:
    def __init__(self, escravos: tuple = ()) -> None:
        self.escravos = escravos


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
    """A multiroom speaker of section 6 with knobs, so a test makes it answer what it wants.

    Uma caixa multiroom da seção 6 com botões, para um teste fazê-la responder o que quer.
    """

    class Falsa(Driver):
        MANIFESTO = _manifesto()
        instancias: list["Falsa"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self.recusa: str | None = None
            self.ignora = False
            self.fora = False
            self.escravo_alheio = False
            # The volume the DEVICE really has, which only a poll can discover.
            # O volume que o APARELHO tem de verdade, que só um poll descobre.
            self.verdade: int | None = None
            self.polls = 0
            self.grupo = _Grupo()
            self._defina(online=True, volume=20, fonte="wifi", fontes=FONTES, tocando=None)
            type(self).instancias.append(self)

        async def atualizar(self) -> None:
            self.polls += 1
            if self.verdade is not None:
                self._defina(volume=self.verdade)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            self.chamadas.append((acao, valor))
            if self.recusa is not None:
                return self.recusa
            if not self.ignora:
                self._aplicar(acao, valor)
            return None

        def _aplicar(self, acao: str, valor: object) -> None:
            if acao == "volume":
                self._defina(volume=valor)
            elif acao == "fonte":
                self._defina(fonte=valor)
            elif acao == "tocar":
                self._defina(tocando=MUSICA)
            elif acao == "pausar":
                self._defina(tocando=None)

        async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
            self.chamadas.append(("entrar_no_grupo", ip_do_mestre))
            return self.recusa

        async def desfazer_grupo(self) -> str | None:
            self.chamadas.append(("desfazer_grupo", None))
            return None

        async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
            self.chamadas.append(("volume_de_escravo", (ip, valor)))
            return None

        async def ler_grupo(self) -> _Grupo:
            return self.grupo

        def marcar_grupo(self, dentro: bool) -> None:
            self.chamadas.append(("marcar_grupo", dentro))

        def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
            self._defina(tocando=tocando, reproduzindo=reproduzindo)

        def e_escravo(self) -> bool:

            return self.escravo_alheio

        def saiu_do_grupo(self) -> bool:
            return self.fora

    return Falsa


def _config(cenas: tuple[Cena, ...] = ()) -> Config:
    return Config(
        equipamentos=(
            Cadastro(identidade="uuid-1", tipo=TIPO, nome="Sala", ip=IP_1),
            Cadastro(identidade="uuid-2", tipo=TIPO, nome="Cozinha", ip=IP_2),
        ),
        blocos=("uuid-1", "uuid-2"),
        cenas=cenas,
    )


@pytest.fixture
def caixas() -> type[Driver]:
    return _fabrica()


@pytest.fixture
def hub(fabrica_cliente, agenda, caixas):
    """A hub with two speakers in blocks 1 and 2 and a clock the test moves by hand.

    Um hub com duas caixas nos blocos 1 e 2 e um relógio que o teste move na mão.
    """

    async def criar(cenas: tuple[Cena, ...] = ()):
        return await fabrica_cliente(
            config=_config(cenas),
            segredos=Segredos(api_token=TOKEN),
            catalogo={TIPO: caixas},
            dormir=agenda.dormir,
            agora=agenda,
        )

    return criar


async def _abrir(cliente, token: str = TOKEN):
    """Connects and authenticates, which is what every frame after the first one needs.

    Conecta e autentica, que é o que todo quadro depois do primeiro precisa.
    """
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(json.dumps({"t": "auth", "token": token}))
    return ws


async def _ler(ws) -> dict:
    mensagem = await ws.receive(timeout=2)
    return json.loads(mensagem.data)


async def _tudo(ws) -> list[dict]:
    """Every frame already on the wire, without waiting for one that never comes.

    Todo quadro já no fio, sem esperar por um que nunca vem.
    """
    quadros = []
    while True:
        try:
            mensagem = await ws.receive(timeout=0.05)
        except TimeoutError:
            return quadros
        if mensagem.data is None or not isinstance(mensagem.data, str):
            return quadros
        quadros.append(json.loads(mensagem.data))


async def _ajustar(ws, dpid: int, valor: object, identificador: int = 1) -> None:
    await ws.send_str(json.dumps({"t": "set", "id": identificador, "dpid": dpid, "v": valor}))


def _reports(quadros: list[dict], dpid: int) -> list[dict]:
    return [q for q in quadros if q.get("t") == "report" and q.get("dpid") == dpid]


def _acks(quadros: list[dict]) -> list[dict]:
    return [q for q in quadros if q.get("t") == "ack"]


async def test_o_snapshot_do_primeiro_frame_traz_so_o_que_pode_ser_reportado(hub):
    cliente = await hub()
    ws = await _abrir(cliente)
    quadro = await _ler(ws)
    assert quadro["t"] == "snapshot"
    dps = quadro["dps"]
    # Why: section 8, the chip never echoes a data point it received, so the preset and the
    # scene are never part of a snapshot; publishing one would state a command as a state.
    # Por que: seção 8, o chip nunca ecoa um data point que recebeu, então o preset e a cena
    # nunca fazem parte de um snapshot; publicar um afirmaria um comando como estado.
    assert str(PRESET_1) not in dps
    assert str(CENA) not in dps
    assert dps[str(ONLINE_1)] is True
    assert dps[str(VOLUME_1)] == 20
    assert dps[str(GRUPO)] == "solo"
    assert json.loads(dps[str(NOMES_BLOCOS)]) == {"z": ["Sala", "Cozinha"]}
    await ws.close()


async def test_um_volume_reporta_otimista_antes_do_ack(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    await _ajustar(ws, VOLUME_1, 42)
    # Why: the bench measured an ack around 30 ms with the report ahead of it, so the bridge
    # sees the volume it asked for at once and not only on the next poll of the speaker.
    # Por que: a bancada mediu um ack em torno de 30 ms com o report na frente, então a ponte
    # vê o volume que pediu na hora e não só no próximo poll da caixa.
    assert await _ler(ws) == {
        "t": "report",
        "dpid": VOLUME_1,
        "v": 42,
        "ts": int(1_700_000_000.0),
    }
    assert await _ler(ws) == {"t": "ack", "id": 1, "ok": True, "code": None}
    assert ("volume", 42) in caixas.instancias[0].chamadas
    await ws.close()


async def test_a_releitura_publica_a_verdade_do_aparelho(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    # Why: a speaker that took the command and did not move is exactly what the reread of
    # section 8 exists for; without it the bridge would hold the optimistic 42 forever.
    # Por que: uma caixa que aceitou o comando e não se mexeu é exatamente para o que a
    # releitura da seção 8 existe; sem ela a ponte guardaria o 42 otimista para sempre.
    caixas.instancias[0].ignora = True
    await _ajustar(ws, VOLUME_1, 42)
    assert (await _tudo(ws))[0]["v"] == 42
    assert await agenda.soltar(RELEITURA_S) == 1
    assert _reports(await _tudo(ws), VOLUME_1) == [
        {"t": "report", "dpid": VOLUME_1, "v": 20, "ts": int(1_700_000_000.0)}
    ]
    await ws.close()


async def test_a_releitura_pergunta_ao_aparelho_e_nao_ao_cache(hub, caixas, agenda):
    """Section 8: the reread is a check against the DEVICE, so it polls it.

    Why: publishing from the cache 1.5 s after the command compared the optimistic value
    against a cache the command itself had written, so the check agreed with the guess every
    time. Here the speaker really sits at 77, which nothing but a poll can discover, and the
    bus has to report 77 and not the 42 that was asked for nor the 20 it had cached.

    Seção 8: a releitura é uma conferência contra o APARELHO, então ela faz o poll dele.

    Por que: publicar do cache 1,5 s depois do comando comparava o valor otimista com um cache
    que o próprio comando escreveu, então a conferência concordava com o palpite toda vez.
    Aqui a caixa está mesmo em 77, o que só um poll descobre, e o barramento precisa reportar
    77 e não o 42 que foi pedido nem o 20 que ele tinha em cache.
    """
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    caixa = caixas.instancias[0]
    caixa.ignora = True
    caixa.verdade = 77
    polls_antes = caixa.polls
    await _ajustar(ws, VOLUME_1, 42)
    assert (await _tudo(ws))[0]["v"] == 42
    assert await agenda.soltar(RELEITURA_S) == 1
    assert caixa.polls > polls_antes, "the reread never asked the speaker anything"
    assert _reports(await _tudo(ws), VOLUME_1) == [
        {"t": "report", "dpid": VOLUME_1, "v": 77, "ts": int(1_700_000_000.0)}
    ]
    await ws.close()


async def test_um_comando_novo_cancela_a_verificacao_pendente(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    caixas.instancias[0].ignora = True
    await _ajustar(ws, VOLUME_1, 42)
    await _tudo(ws)
    await _ajustar(ws, VOLUME_1, 43, identificador=2)
    await _tudo(ws)
    # Why: two verifications of the same data point would publish the older reading after the
    # newer one, and the bridge would watch the volume bounce back on its own.
    # Por que: duas verificações do mesmo data point publicariam a leitura antiga depois da
    # nova, e a ponte veria o volume voltar sozinho.
    assert _pendentes(VOLUME_1) == 1
    assert await agenda.soltar(RELEITURA_S) == 1
    assert len(_reports(await _tudo(ws), VOLUME_1)) == 1
    await ws.close()


async def test_um_preset_e_aceito_e_nunca_reportado(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    await _ajustar(ws, PRESET_1, "cmd3")
    quadros = await _tudo(ws)
    assert _acks(quadros) == [{"t": "ack", "id": 1, "ok": True, "code": None}]
    # Why: section 8, DP 103 is send only because the chip never echoes; a report of it would
    # publish a preset as if a speaker had confirmed it.
    # Por que: seção 8, o DP 103 é só de envio porque o chip nunca ecoa; um report dele
    # publicaria um preset como se uma caixa o tivesse confirmado.
    assert _reports(quadros, PRESET_1) == []
    assert ("comando_extra", "preset:3") in caixas.instancias[0].chamadas
    await agenda.soltar(RELEITURA_S)
    assert _reports(await _tudo(ws), PRESET_1) == []
    await ws.close()


async def test_uma_cena_roda_pelo_dp_131_e_nao_e_reportada(hub, caixas):
    cliente = await hub((Cena(nome="Noite", passos=(Passo(dpid=VOLUME_2, valor=7),)),))
    ws = await _abrir(cliente)
    snapshot = await _ler(ws)
    assert json.loads(snapshot["dps"][str(NOMES_CENAS)]) == {"c": ["Noite"]}
    await _ajustar(ws, CENA, "cena1")
    quadros = await _tudo(ws)
    assert _acks(quadros) == [{"t": "ack", "id": 1, "ok": True, "code": None}]
    assert _reports(quadros, CENA) == []
    assert ("volume", 7) in caixas.instancias[1].chamadas
    await ws.close()


async def test_o_grupo_do_dp_132_forma_pelo_barramento(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    await _ajustar(ws, GRUPO, "grupo1")
    quadros = await _tudo(ws)
    assert _acks(quadros) == [{"t": "ack", "id": 1, "ok": True, "code": None}]
    assert _reports(quadros, GRUPO)[-1]["v"] == "grupo1"
    # Why: section 14, the slave joins the MASTER by its address and the master is never the
    # one asked to join, because a group is formed by naming who leads it.
    # Por que: seção 14, o escravo entra no MESTRE pelo endereço dele e o mestre nunca é o
    # chamado a entrar, porque um grupo é formado nomeando quem o lidera.
    assert ("entrar_no_grupo", IP_1) in caixas.instancias[1].chamadas
    assert not [c for c in caixas.instancias[0].chamadas if c[0] == "entrar_no_grupo"]
    await ws.close()


async def test_a_entrada_da_bloco_aceita_so_o_que_o_hardware_declara(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    await _ajustar(ws, ENTRADA_1, "line-in")
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 1, "ok": True, "code": None}]
    # Why: section 14, only the inputs plm_support declares exist on that hardware; a bus that
    # guessed would command an input the speaker does not have.
    # Por que: seção 14, só as entradas que o plm_support declara existem naquele hardware; um
    # barramento que adivinhasse comandaria uma entrada que a caixa não tem.
    await _ajustar(ws, ENTRADA_1, "hdmi", identificador=2)
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 2, "ok": False, "code": "valor_invalido"}]
    assert [c for c in caixas.instancias[0].chamadas if c[0] == "fonte"] == [("fonte", "line-in")]
    await ws.close()


async def test_um_quadro_ruim_e_recusado_e_o_socket_segue_vivo(hub):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    for bruto in ("nao e json", "[]", '{"t":"nada"}', '{"t":"set","dpid":"101","v":1}'):
        await ws.send_str(bruto)
        assert _acks(await _tudo(ws)) == [
            {"t": "ack", "id": None, "ok": False, "code": "frame_invalido"}
        ]
    # Why: the other end is whatever bridge somebody implemented from the public contract, and
    # one bad frame must not drop a socket that is carrying six blocks.
    # Por que: do outro lado está a ponte que alguém implementou do contrato público, e um
    # quadro ruim não pode derrubar um socket que carrega seis blocos.
    await _ajustar(ws, VOLUME_1, 30, identificador=9)
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 9, "ok": True, "code": None}]
    await ws.close()


async def test_um_dp_fora_do_contrato_e_recusado_com_o_codigo_estavel(hub):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    await _ajustar(ws, 999, 1)
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 1, "ok": False, "code": "dp_desconhecido"}]
    await ws.close()


async def test_um_report_vai_para_todos_os_clientes(hub):
    cliente = await hub()
    primeiro = await _abrir(cliente)
    segundo = await _abrir(cliente)
    await _ler(primeiro)
    await _ler(segundo)
    await _ajustar(primeiro, VOLUME_1, 33)
    assert _reports(await _tudo(primeiro), VOLUME_1)[0]["v"] == 33
    # Why: the bus is a broadcast, so a second bridge (or the panel of another integrator)
    # sees the same state and not a hub that answers only whoever commanded it.
    # Por que: o barramento é difusão, então uma segunda ponte (ou o painel de outro
    # integrador) vê o mesmo estado e não um hub que responde só a quem o comandou.
    assert _reports(await _tudo(segundo), VOLUME_1)[0]["v"] == 33
    await primeiro.close()
    await segundo.close()


async def test_o_estado_que_o_aparelho_muda_sozinho_e_publicado_no_tique(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    caixas.instancias[0]._defina(volume=61)
    assert await agenda.soltar(INTERVALO_S) == 1
    assert _reports(await _tudo(ws), VOLUME_1) == [
        {"t": "report", "dpid": VOLUME_1, "v": 61, "ts": int(1_700_000_000.0)}
    ]
    # Why: a report is only ever born of real state, and a state that did not change is not a
    # report; a bus that republished everything on every tick would be noise on the platform.
    # Por que: um report só nasce de estado real, e um estado que não mudou não é report; um
    # barramento que republicasse tudo a cada tique seria ruído na plataforma.
    assert await agenda.soltar(INTERVALO_S) == 1
    assert _reports(await _tudo(ws), VOLUME_1) == []
    await ws.close()


async def test_o_titulo_da_bloco_respeita_o_limite_de_cinco_segundos(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    caixas.instancias[0]._defina(tocando="Primeira")
    await agenda.soltar(INTERVALO_S)
    assert _reports(await _tudo(ws), TOCANDO_1)[0]["v"] == "Primeira"
    # Why: section 8 throttles DP 105 to one report every five seconds, because a speaker
    # changing track fills the platform with strings nobody reads.
    # Por que: a seção 8 limita o DP 105 a um report a cada cinco segundos, porque uma caixa
    # trocando de faixa enche a plataforma de strings que ninguém lê.
    caixas.instancias[0]._defina(tocando="Segunda")
    await agenda.soltar(INTERVALO_S)
    assert _reports(await _tudo(ws), TOCANDO_1) == []
    # Why: the reading that was held is NOT lost, it is published on the first tick after the
    # throttle; losing it would leave the bridge showing a track that already ended.
    # Por que: a leitura segurada NÃO se perde, ela sai no primeiro tique depois do limite;
    # perdê-la deixaria a ponte mostrando uma faixa que já acabou.
    agenda.avancar(6.0)
    await agenda.soltar(INTERVALO_S)
    assert _reports(await _tudo(ws), TOCANDO_1)[0]["v"] == "Segunda"
    await ws.close()


async def test_um_get_sem_upgrade_responde_um_codigo_estavel(hub):
    cliente = await hub()
    resposta = await cliente.get(CAMINHO)
    # Why: section 11, the daemon answers a code the panel translates and never the phrase the
    # library raises, which would leave the gate as prose.
    # Por que: seção 11, o daemon responde um código que o painel traduz e nunca a frase que a
    # biblioteca estoura, que sairia do portão como prosa.
    assert resposta.status == 426
    assert await resposta.json() == {"ok": False, "code": "requer_websocket"}


async def test_um_driver_que_estoura_vira_codigo_estavel_e_o_socket_segue(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ler(ws)
    caixas.instancias[0].recusa = "erro_aparelho"
    await _ajustar(ws, VOLUME_1, 50)
    # Why: a speaker that refused is not a state to publish, so the ack carries the code of
    # section 6 and no report goes out claiming the volume changed.
    # Por que: uma caixa que recusou não é estado para publicar, então o ack leva o código da
    # seção 6 e nenhum report sai dizendo que o volume mudou.
    quadros = await _tudo(ws)
    assert _acks(quadros) == [{"t": "ack", "id": 1, "ok": False, "code": "erro_aparelho"}]
    assert _reports(quadros, VOLUME_1) == []
    await ws.close()


async def test_um_defeito_nosso_vira_codigo_estavel_e_nunca_uma_excecao():
    async def explode(dpid: object, valor: object) -> str | None:
        raise RuntimeError("quebrei")

    # Why: an exception out of here would leave the client waiting for an ack that never comes
    # and would take down a socket that is carrying six blocks.
    # Por que: uma exceção saindo daqui deixaria o cliente esperando por um ack que nunca vem e
    # derrubaria um socket que carrega seis blocos.
    barramento = Barramento(explode, dict, lambda: TOKEN)
    assert await barramento.aplicar(VOLUME_1, 10) == "erro_interno"


def _pendentes(dpid: int) -> int:
    nome = f"dpbus:verifica:{dpid}"
    return len([t for t in asyncio.all_tasks() if t.get_name() == nome and not t.done()])


async def test_uma_bloco_esvaziada_corrige_o_que_ja_tinha_reportado(hub, agenda):
    """Section 8: a block whose speaker was removed stops producing values, and the last
    thing published about it must not stand forever.

    Why: the publish loop only walks the values it has, so an emptied block was never visited
    again and the bridge kept showing DP 104 online, at a volume, for a block with no speaker.

    Seção 8: um bloco cuja caixa foi removida para de produzir valores, e o último publicado a
    respeito dele não pode ficar valendo para sempre.

    Por que: o laço de publicação só caminha pelos valores que tem, então um bloco esvaziado
    nunca era visitado de novo e a ponte seguia mostrando o DP 104 online, num volume, para
    um bloco sem caixa.
    """
    cliente = await hub()
    ws = await _abrir(cliente)
    snapshot = await _ler(ws)
    assert snapshot["dps"][str(ONLINE_1)] is True
    assert snapshot["dps"][str(VOLUME_1)] == 20
    # The loop of the bus publishes once a second, which is what records what the bridge holds.
    # O laço do barramento publica uma vez por segundo, que é o que anota o que a ponte tem.
    await cliente.app[BARRAMENTO].publicar()
    await _tudo(ws)

    await cliente.app[BLOCOS].esquecer("uuid-1")
    await cliente.app[BARRAMENTO].publicar()

    quadros = await _tudo(ws)
    reportados = {q["dpid"]: q["v"] for q in quadros if q["t"] == "report"}
    assert reportados.get(ONLINE_1) is False, quadros
    assert reportados.get(VOLUME_1) == 0
    # DP 105 was already the empty string, and section 8 never reports a value that did not
    # change, so it is right for it to be absent here.
    # O DP 105 já era a string vazia, e a seção 8 nunca reporta valor que não mudou, então é
    # certo ele estar ausente aqui.
    assert reportados.get(TOCANDO_1, "") == ""
    await ws.close()
