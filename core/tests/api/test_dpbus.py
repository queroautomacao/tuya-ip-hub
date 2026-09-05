# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the DP-bus over a real socket: the first frame with its licence, the
consulta and its snapshot, the acks, the optimistic report with its reread and the report
policy of section 8, licence by licence.

Everything here talks to the daemon the way the bridge of one licence will: a WebSocket,
JSON frames, the api_token of section 9 and the id of the licence on the FIRST frame. The
equipment is fake, of the driver contract of section 6, never a real one, and the three
waits of the bus (the five seconds of the auth, the second and a half of the reread and the
tick of one second) are moved by hand, so this file is fast and deterministic and no test
sleeps.

The data point numbers and the numbers of the report policy are written by hand here. A
test that asked the map for them would agree with any change the map made to the contract
of section 8, which is exactly what a contract test exists to catch.

O contrato do DP-bus sobre um socket de verdade: o primeiro quadro com a licença dele, a
consulta e o snapshot dela, os acks, o report otimista com a releitura e a política de
reports da seção 8, licença por licença.

Tudo aqui fala com o daemon do jeito que a ponte de uma licença vai falar: um WebSocket,
quadros JSON, o api_token da seção 9 e o id da licença no PRIMEIRO quadro. Os equipamentos
são falsos, do contrato de driver da seção 6, nunca um de verdade, e as três esperas do
barramento (os cinco segundos do auth, o segundo e meio da releitura e o tique de um segundo)
são movidas na mão, então este arquivo é rápido e determinístico e nenhum teste dorme.

Os números de data point e os números da política de reports são escritos na mão aqui. Um
teste que os pedisse ao mapa concordaria com qualquer mudança que o mapa fizesse no contrato
da seção 8, que é exatamente o que um teste de contrato existe para pegar.
"""

import asyncio
import json
import logging

import pytest
from aiohttp import WSMsgType

from iphub.api.comum import CENAS, LICENCAS, SEGREDOS
from iphub.cenas import Cena, Passo
from iphub.config import Cadastro, Config, Item, Licenca
from iphub.dpbus import mapa
from iphub.dpbus.socket import BARRAMENTO, Barramento
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import MODOS_AR, VENTOS, Manifesto
from iphub.segredos import Segredos

CAMINHO = "/dpbus"
TIPO = "multiroom_falso"
TIPO_AR = "ar_falso"
TOKEN = "token-de-maquina-so-deste-teste"
IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"
IP_AR = "192.0.2.21"
MUSICA = "Musica 1, Artista"

# The licences of the hub under test: one of audio and video, a second one of the same
# product for the isolation tests, and one of air.
# As licenças do hub sob teste: uma de áudio e vídeo, uma segunda do mesmo produto para os
# testes de isolamento, e uma de ar.
AV = "av1"
OUTRA = "av2"
AR = "ar1"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")
CAPACIDADES_AR = ("ligar", "desligar", "temperatura", "modo", "vento")
ENTRADAS_DO_CADASTRO = (Item(rotulo="Wi-Fi", valor="wifi"), Item(rotulo="Linha", valor="line-in"))

# The numbers of section 8, written by hand on purpose: the product of audio and video.
# Os números da seção 8, escritos na mão de propósito: o produto de áudio e vídeo.
LIGADO_1 = 101
NIVEL_1, NIVEL_2 = 121, 122
CENA_AV, GRUPO, COMANDO = 141, 142, 143
ONLINE, MUDOS, ENTRADAS, MODOS, TITULOS = 144, 145, 146, 147, 148
PERFIS_1, NOMES_CENAS_1, NOMES_CENAS_2 = 149, 154, 155

# The product of air: machine 1 starts at 101, the installation at 171.
# O produto de ar: a máquina 1 começa em 101, a instalação em 171.
AR_LIGADO_1, AR_TEMPERATURA_1, AR_MODO_1, AR_VENTO_1 = 101, 102, 103, 104
CENA_AR, AR_ONLINE, AR_NOMES, AR_NOMES_CENAS_1 = 171, 172, 173, 174

# The report policy of section 8, by hand: the windows, the daily warning and the day.
# A política de reports da seção 8, na mão: as janelas, o aviso do dia e o dia.
JANELA_A_S = 2.0
JANELA_B_S = 10.0
AVISO_DO_DIA = 250
JANELA_APERTADA_S = 30.0
SEGUNDOS_POR_DIA = 86_400

# The three waits of the bus, which the fake clock releases by name.
# As três esperas do barramento, que o relógio falso solta pelo nome.
PRAZO_AUTH_S = 5.0
RELEITURA_S = 1.5
INTERVALO_S = 1.0

# Far past every window, so a change after it is judged on its own.
# Bem depois de toda janela, para uma mudança depois dela ser julgada sozinha.
FOLGA_S = 60.0

TS = int(1_700_000_000.0)
OK = {"t": "ack", "id": 1, "ok": True, "code": None}


class _Grupo:
    def __init__(self, escravos: tuple = ()) -> None:
        self.escravos = escravos


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
            self.no_grupo = False
            # The volume the DEVICE really has, which only a poll can discover.
            # O volume que o APARELHO tem de verdade, que só um poll descobre.
            self.verdade: int | None = None
            self.polls = 0
            self.grupo = _Grupo()
            self._defina(online=True, volume=20, fonte="wifi", tocando=None)
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
            elif acao == "mudo":
                self._defina(mudo=valor)
            elif acao == "fonte":
                self._defina(fonte=valor)
            elif acao == "tocar":
                self._defina(tocando=MUSICA, reproduzindo=True)
            elif acao == "pausar":
                self._defina(reproduzindo=False)

        async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
            self.chamadas.append(("entrar_no_grupo", ip_do_mestre))
            return self.recusa

        async def tirar_do_grupo(self, ip_do_escravo: object) -> str | None:
            self.chamadas.append(("tirar_do_grupo", ip_do_escravo))
            return None

        async def desfazer_grupo(self) -> str | None:
            self.chamadas.append(("desfazer_grupo", None))
            return None

        async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
            self.chamadas.append(("volume_de_escravo", (ip, valor)))
            return None

        async def ler_grupo(self) -> _Grupo:
            return self.grupo

        def marcar_grupo(self, dentro: bool) -> None:
            self.no_grupo = dentro

        def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
            self._defina(tocando=tocando, reproduzindo=reproduzindo)

        def e_escravo(self) -> bool:
            return self.escravo_alheio

        def saiu_do_grupo(self) -> bool:
            return self.fora

    return Falsa


def _fabrica_ar() -> type[Driver]:
    """An air conditioner of section 6 that does what it is told and writes it down.

    Um ar condicionado da seção 6 que faz o que mandam e anota.
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
            if acao == "ligar":
                self._defina(ligado=True)
            elif acao == "desligar":
                self._defina(ligado=False)
            elif acao == "temperatura":
                self._defina(temperatura=valor)
            elif acao == "modo":
                self._defina(modo=valor)
            elif acao == "vento":
                self._defina(vento=valor)
            return None

    return Ar


def _caixa(identidade: str, nome: str, ip: str) -> Cadastro:
    return Cadastro(
        identidade=identidade,
        tipo=TIPO,
        nome=nome,
        ip=ip,
        listas={"entradas": ENTRADAS_DO_CADASTRO},
    )


SALA = _caixa("uuid-1", "Sala", IP_1)
COZINHA = _caixa("uuid-2", "Cozinha", IP_2)
QUARTO = Cadastro(identidade="uuid-ar", tipo=TIPO_AR, nome="Quarto", ip=IP_AR)


# The licences of a test and the numbers of each one, as (produto, ordem).
# As licenças de um teste e os números de cada uma, como (produto, ordem).
UMA_LICENCA = {AV: ("av", ("uuid-1", "uuid-2"))}
DUAS_LICENCAS = {AV: ("av", ("uuid-1",)), OUTRA: ("av", ("uuid-2",))}
COM_AR = {**UMA_LICENCA, AR: ("ar", ("uuid-ar",))}


def _config(
    cenas: tuple[Cena, ...] = (),
    licencas: dict[str, tuple[str, tuple[str, ...]]] = UMA_LICENCA,
) -> Config:
    """Two speakers and an air conditioner registered, and the licences of the test with
    their numbers, the way config.json holds them.

    Duas caixas e um ar condicionado cadastrados, e as licenças do teste com os números
    delas, do jeito que o config.json as guarda.
    """
    return Config(
        equipamentos=(SALA, COZINHA, QUARTO),
        licencas=tuple(
            Licenca(id=id_licenca, produto=produto) for id_licenca, (produto, _) in licencas.items()
        ),
        numeros={id_licenca: ordem for id_licenca, (_, ordem) in licencas.items()},
        cenas=cenas,
    )


@pytest.fixture
def caixas() -> type[Driver]:
    return _fabrica()


@pytest.fixture
def ares() -> type[Driver]:
    return _fabrica_ar()


@pytest.fixture
def hub(fabrica_cliente, agenda, caixas, ares):
    """A hub with the fake drivers in its catalog, the licences of the test in its book and a
    clock the test moves by hand.

    Um hub com os drivers falsos no catálogo, as licenças do teste no livro e um relógio que
    o teste move na mão.
    """

    async def criar(
        cenas: tuple[Cena, ...] = (),
        licencas: dict[str, tuple[str, tuple[str, ...]]] = UMA_LICENCA,
    ):
        return await fabrica_cliente(
            config=_config(cenas, licencas),
            segredos=Segredos(api_token=TOKEN),
            catalogo={TIPO: caixas, TIPO_AR: ares},
            dormir=agenda.dormir,
            agora=agenda,
        )

    return criar


def _auth(token: str = TOKEN, licenca: str = AV) -> str:
    return json.dumps({"t": "auth", "token": token, "licenca": licenca})


async def _abrir(cliente, licenca: str = AV, token: str = TOKEN):
    """Connects and authenticates for one licence, which is what every frame after the first
    one needs.

    Conecta e autentica para uma licença, que é o que todo quadro depois do primeiro precisa.
    """
    ws = await cliente.ws_connect(CAMINHO)
    await ws.send_str(_auth(token, licenca))
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


async def _ate_fechar(ws, prazo_s: float = 2.0) -> None:
    """Waits for the socket to be gone, past whatever reports were already on the way.

    Espera o socket ter acabado, passando os reports que já estavam a caminho.
    """
    while True:
        mensagem = await ws.receive(timeout=prazo_s)
        if mensagem.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
            return


async def _ajustar(ws, dpid: int, valor: object, identificador: int = 1) -> None:
    await ws.send_str(json.dumps({"t": "set", "id": identificador, "dpid": dpid, "v": valor}))


async def _consultar(ws, identificador: object = 1) -> dict:
    """Asks for the snapshot of the licence and reads past whatever report came first.

    Pede o snapshot da licença e passa por qualquer report que tenha chegado antes.
    """
    await ws.send_str(json.dumps({"t": "consulta", "id": identificador}))
    while True:
        quadro = await _ler(ws)
        if quadro["t"] == "snapshot":
            return quadro


async def _assentar(agenda, *sockets) -> None:
    """One tick, so the bus records what the bridge already holds, then the clock moves past
    every window of section 8, so the next change is judged on its own.

    Um tique, para o barramento anotar o que a ponte já tem, e depois o relógio passa de toda
    janela da seção 8, para a próxima mudança ser julgada sozinha.
    """
    assert await agenda.soltar(INTERVALO_S) == 1
    for ws in sockets:
        await _tudo(ws)
    agenda.avancar(FOLGA_S)


def _reports(quadros: list[dict], dpid: int) -> list[dict]:
    return [q for q in quadros if q.get("t") == "report" and q.get("dpid") == dpid]


def _reportados(quadros: list[dict]) -> dict[int, object]:
    return {q["dpid"]: q["v"] for q in quadros if q.get("t") == "report"}


def _acks(quadros: list[dict]) -> list[dict]:
    return [q for q in quadros if q.get("t") == "ack"]


def _pendentes(licenca: str, dpid: int) -> int:
    nome = f"dpbus:verifica:{licenca}:{dpid}"
    return len([t for t in asyncio.all_tasks() if t.get_name() == nome and not t.done()])


async def test_nao_ha_snapshot_na_subida_e_a_consulta_traz_a_fatia_da_licenca(hub):
    cliente = await hub()
    ws = await _abrir(cliente)
    # Why: section 8, there is no burst on the way up; a bridge that wants the state asks.
    # Por que: seção 8, não há rajada na subida; uma ponte que quer o estado pergunta.
    assert await _tudo(ws) == []
    quadro = await _consultar(ws, identificador="pedido-7")
    assert quadro["t"] == "snapshot"
    assert quadro["id"] == "pedido-7"
    dps = quadro["dps"]
    # Why: a JSON object key is a string, so a bridge reads dps["121"] in any language.
    # Por que: chave de objeto JSON é string, então uma ponte lê dps["121"] em qualquer
    # linguagem.
    assert all(isinstance(chave, str) for chave in dps)
    # Why: the chip never echoes a data point it received, so the scene and the command
    # channel are never part of a snapshot; publishing one would state an order as a state.
    # Por que: o chip nunca ecoa um data point que recebeu, então a cena e o canal de comando
    # nunca fazem parte de um snapshot; publicar um afirmaria uma ordem como estado.
    assert str(CENA_AV) not in dps
    assert str(COMANDO) not in dps
    # Why: section 8, an always-on equipment stays silent on its power data point.
    # Por que: seção 8, um equipamento always-on fica calado no data point de ligar.
    assert str(LIGADO_1) not in dps
    assert dps[str(NIVEL_1)] == 20
    assert dps[str(NIVEL_2)] == 20
    assert dps[str(GRUPO)] == 0
    assert dps[str(ONLINE)] == 0b11
    assert dps[str(MUDOS)] == 0
    assert dps[str(ENTRADAS)] == "1=1;2=1"
    assert dps[str(MODOS)] == ""
    assert dps[str(TITULOS)] == ""
    assert dps[str(PERFIS_1)] == ("1|au|Sala|Wi-Fi,Linha|||NMEPG;2|au|Cozinha|Wi-Fi,Linha|||NMEPG")
    assert json.loads(dps[str(NOMES_CENAS_1)]) == {"c": []}
    assert json.loads(dps[str(NOMES_CENAS_2)]) == {"c": []}
    await ws.close()


async def test_a_consulta_nao_conta_como_report(hub, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    barramento = cliente.app[BARRAMENTO]
    # Why: only a change reaches the bridge, so one set is what makes the day cost a report.
    # Por que: só uma mudança chega à ponte, então um set é o que faz o dia custar um report.
    await _ajustar(ws, NIVEL_2, 33)
    await _tudo(ws)
    antes = barramento.reports_do_dia(AV)
    assert antes > 0
    await _consultar(ws, identificador=1)
    await _consultar(ws, identificador=2)
    # Why: section 14, a report the platform asked for does not count against the day, so
    # the panel may consult as often as it likes without eating the budget of the licence.
    # Por que: seção 14, um report que a plataforma pediu não conta contra o dia, então o
    # painel pode consultar quantas vezes quiser sem comer o orçamento da licença.
    assert barramento.reports_do_dia(AV) == antes
    await ws.close()


async def test_o_prazo_do_auth_e_solto_quando_o_primeiro_quadro_chega(hub, agenda):
    cliente = await hub()
    ws = await cliente.ws_connect(CAMINHO)
    await agenda.girar()
    assert agenda.presas(PRAZO_AUTH_S) == 1
    await ws.send_str(_auth())
    await _consultar(ws)
    # Why: the deadline of section 8 is for the socket that says nothing; once the first
    # frame authenticated, a timer left running would close a bridge that did everything
    # right, five seconds after it connected.
    # Por que: o prazo da seção 8 é para o socket que não fala; depois de o primeiro quadro
    # autenticar, um temporizador deixado rodando fecharia uma ponte que fez tudo certo,
    # cinco segundos depois de conectar.
    assert agenda.presas(PRAZO_AUTH_S) == 0
    assert cliente.app[BARRAMENTO].ouvintes_de(AV) == 1
    await ws.close()


async def test_um_nivel_reporta_otimista_antes_do_ack(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ajustar(ws, NIVEL_1, 42)
    # Why: the bench measured an ack around 30 ms with the report ahead of it, so the bridge
    # sees the level it asked for at once and not only on the next poll of the speaker.
    # Por que: a bancada mediu um ack em torno de 30 ms com o report na frente, então a ponte
    # vê o nível que pediu na hora e não só no próximo poll da caixa.
    assert await _ler(ws) == {"t": "report", "dpid": NIVEL_1, "v": 42, "ts": TS}
    assert await _ler(ws) == OK
    assert ("volume", 42) in caixas.instancias[0].chamadas
    await ws.close()


async def test_a_releitura_pergunta_ao_aparelho_e_publica_a_verdade(hub, caixas, agenda):
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
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    caixa.ignora = True
    caixa.verdade = 77
    polls_antes = caixa.polls
    await _ajustar(ws, NIVEL_1, 42)
    assert _reports(await _tudo(ws), NIVEL_1)[0]["v"] == 42
    # Why: the reread publishes under the policy of section 8, and the optimistic report
    # opened the window of the level; real time passes here the way it does on the bench.
    # Por que: a releitura publica sob a política da seção 8, e o report otimista abriu a
    # janela do nível; o tempo real passa aqui do jeito que passa na bancada.
    agenda.avancar(JANELA_A_S)
    assert await agenda.soltar(RELEITURA_S) == 1
    assert caixa.polls > polls_antes, "the reread never asked the speaker anything"
    assert _reports(await _tudo(ws), NIVEL_1) == [
        {"t": "report", "dpid": NIVEL_1, "v": 77, "ts": int(agenda())}
    ]
    await ws.close()


async def test_a_releitura_cala_quando_o_aparelho_obedeceu(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    await _ajustar(ws, NIVEL_1, 42)
    assert _reports(await _tudo(ws), NIVEL_1)[0]["v"] == 42
    agenda.avancar(JANELA_A_S)
    polls_antes = caixa.polls
    assert await agenda.soltar(RELEITURA_S) == 1
    assert caixa.polls > polls_antes
    # Why: section 8, the reread reports only when the device diverged; a second report of
    # the same 42 would be a repeat, which the policy never sends.
    # Por que: seção 8, a releitura reporta só quando o aparelho divergiu; um segundo report
    # do mesmo 42 seria uma repetição, que a política nunca manda.
    assert await _tudo(ws) == []
    await ws.close()


async def test_a_verdade_da_releitura_espera_a_janela_e_sai_no_tique_seguinte(hub, caixas, agenda):
    """The optimistic report opens the 2 s window of the level, so a reread at 1.5 s that
    found the device elsewhere holds its reading, and the first tick after the window sends
    it: the last value of the window wins and nothing is lost.

    O report otimista abre a janela de 2 s do nível, então uma releitura em 1,5 s que achou o
    aparelho em outro lugar segura a leitura, e o primeiro tique depois da janela a manda: o
    último valor da janela vence e nada se perde.
    """
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    caixa.ignora = True
    caixa.verdade = 77
    await _ajustar(ws, NIVEL_1, 42)
    assert _reports(await _tudo(ws), NIVEL_1)[0]["v"] == 42
    agenda.avancar(RELEITURA_S)
    polls_antes = caixa.polls
    assert await agenda.soltar(RELEITURA_S) == 1
    assert caixa.polls > polls_antes
    assert _reports(await _tudo(ws), NIVEL_1) == []
    agenda.avancar(JANELA_A_S - RELEITURA_S)
    assert await agenda.soltar(INTERVALO_S) == 1
    assert [q["v"] for q in _reports(await _tudo(ws), NIVEL_1)] == [77]
    await ws.close()


async def test_um_comando_novo_cancela_a_verificacao_pendente(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixas.instancias[0].ignora = True
    await _ajustar(ws, NIVEL_1, 42)
    await _tudo(ws)
    await _ajustar(ws, NIVEL_1, 43, identificador=2)
    await _tudo(ws)
    # Why: two verifications of the same data point would publish the older reading after the
    # newer one, and the bridge would watch the level bounce back on its own.
    # Por que: duas verificações do mesmo data point publicariam a leitura antiga depois da
    # nova, e a ponte veria o nível voltar sozinho.
    assert _pendentes(AV, NIVEL_1) == 1
    agenda.avancar(JANELA_A_S)
    assert await agenda.soltar(RELEITURA_S) == 1
    assert [q["v"] for q in _reports(await _tudo(ws), NIVEL_1)] == [20]
    await ws.close()


async def test_um_set_no_canal_de_comando_e_aceito_e_nunca_reportado(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    await _ajustar(ws, COMANDO, "1:extra:preset3")
    quadros = await _tudo(ws)
    assert _acks(quadros) == [OK]
    # Why: section 8, DP 143 is send only because the chip never echoes; a report of it would
    # publish a command as if a speaker had confirmed it.
    # Por que: seção 8, o DP 143 é só de envio porque o chip nunca ecoa; um report dele
    # publicaria um comando como se uma caixa o tivesse confirmado.
    assert _reports(quadros, COMANDO) == []
    assert ("comando_extra", "preset3") in caixas.instancias[0].chamadas
    agenda.avancar(JANELA_A_S)
    assert await agenda.soltar(RELEITURA_S) == 1
    assert _reports(await _tudo(ws), COMANDO) == []
    await ws.close()


async def test_o_canal_de_comando_rele_o_equipamento_do_numero_nomeado(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    sala, cozinha = caixas.instancias
    await _ajustar(ws, COMANDO, "1:entrada:2")
    assert _acks(await _tudo(ws)) == [OK]
    # Why: section 8, the index of the command is the position in the list of the
    # registration, and the driver receives the value of that item, never the index.
    # Por que: seção 8, o índice do comando é a posição na lista do cadastro, e o driver
    # recebe o valor daquele item, nunca o índice.
    assert ("fonte", "line-in") in sala.chamadas
    polls = (sala.polls, cozinha.polls)
    assert await agenda.soltar(RELEITURA_S) == 1
    # Why: the command names a number, so the reread asks THAT equipment and not the others;
    # polling the whole licence for one command would be a poll per command per speaker.
    # Por que: o comando nomeia um número, então a releitura pergunta ÀQUELE equipamento e não
    # aos outros; fazer o poll da licença inteira por um comando seria um poll por comando por
    # caixa.
    assert sala.polls == polls[0] + 1
    assert cozinha.polls == polls[1]
    assert [q["v"] for q in _reports(await _tudo(ws), ENTRADAS)] == ["1=2;2=1"]
    await ws.close()


async def test_o_canal_de_comando_traduz_para_a_capacidade_e_recusa_o_resto(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    sala = caixas.instancias[0]
    await _ajustar(ws, COMANDO, "1:mudo", identificador=1)
    await _ajustar(ws, COMANDO, "1:mudo", identificador=2)
    await _ajustar(ws, COMANDO, "1:tocar", identificador=3)
    quadros = await _tudo(ws)
    assert [q["ok"] for q in _acks(quadros)] == [True, True, True]
    # Why: section 8, the mute of the channel toggles, because the panel has one button and
    # the state comes back by the report of the muted bits.
    # Por que: seção 8, o mudo do canal alterna, porque o painel tem um botão só e o estado
    # volta pelo report dos bits de mudo.
    assert sala.chamadas == [("mudo", True), ("mudo", False), ("tocar", None)]
    await _ajustar(ws, COMANDO, "1:tecla:ok", identificador=4)
    await _ajustar(ws, COMANDO, "1:entrada:9", identificador=5)
    await _ajustar(ws, COMANDO, "3:mudo", identificador=6)
    # Why: what the manifest does not declare is refused before the driver is touched, an
    # index outside the list is a value the data point does not take, and an empty number
    # reaches no equipment; each one is its own stable code, so the panel says which.
    # Por que: o que o manifesto não declara é recusado antes de tocar no driver, um índice
    # fora da lista é um valor que o data point não aceita, e um número vazio não alcança
    # equipamento nenhum; cada um é o próprio código estável, para o painel dizer qual.
    assert [(q["id"], q["code"]) for q in _acks(await _tudo(ws))] == [
        (4, "nao_suportado"),
        (5, "valor_invalido"),
        (6, "numero_offline"),
    ]
    assert len(sala.chamadas) == 3
    await ws.close()


async def test_uma_cena_roda_pelo_dp_de_cena_e_nao_e_reportada(hub, caixas):
    cena = Cena(nome="Noite", passos=(Passo(equipamento="uuid-2", acao="volume", valor=7),))
    cliente = await hub(cenas=(cena,))
    ws = await _abrir(cliente)
    dps = (await _consultar(ws))["dps"]
    assert json.loads(dps[str(NOMES_CENAS_1)]) == {"c": ["Noite"]}
    await _ajustar(ws, CENA_AV, 1)
    quadros = await _tudo(ws)
    assert _acks(quadros) == [OK]
    assert _reports(quadros, CENA_AV) == []
    assert ("volume", 7) in caixas.instancias[1].chamadas
    # Why: the scene data point carries a number, and a number nobody wrote a scene for is
    # refused with the code of the scenes and never run as an empty scene.
    # Por que: o data point de cena leva um número, e um número para o qual ninguém escreveu
    # cena é recusado com o código das cenas e nunca rodado como cena vazia.
    await _ajustar(ws, CENA_AV, 2, identificador=2)
    assert _acks(await _tudo(ws)) == [
        {"t": "ack", "id": 2, "ok": False, "code": "cena_nao_encontrada"}
    ]
    await ws.close()


async def test_a_mesma_cena_dispara_de_qualquer_licenca(hub, caixas):
    cena = Cena(nome="Noite", passos=(Passo(equipamento="uuid-2", acao="volume", valor=7),))
    cliente = await hub(cenas=(cena,), licencas=COM_AR)
    ws = await _abrir(cliente, AR)
    dps = (await _consultar(ws))["dps"]
    assert json.loads(dps[str(AR_NOMES_CENAS_1)]) == {"c": ["Noite"]}
    await _ajustar(ws, CENA_AR, 1)
    quadros = await _tudo(ws)
    assert _acks(quadros) == [OK]
    assert _reports(quadros, CENA_AR) == []
    # Why: section 8, the scenes belong to the hub and are the same in every licence, so the
    # automation of the platform fires scene 1 through whichever device it has at hand.
    # Por que: seção 8, as cenas são do hub e iguais em toda licença, então a automação da
    # plataforma dispara a cena 1 pelo dispositivo que tiver à mão.
    assert ("volume", 7) in caixas.instancias[1].chamadas
    await ws.close()


async def test_o_grupo_do_dp_142_forma_e_desfaz_pelo_barramento(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    sala, cozinha = caixas.instancias
    await _ajustar(ws, GRUPO, 1)
    quadros = await _tudo(ws)
    assert _acks(quadros) == [OK]
    assert [q["v"] for q in _reports(quadros, GRUPO)] == [1]
    # Why: section 14, the slave joins the MASTER by its address and the master is never the
    # one asked to join, because a group is formed by naming who leads it.
    # Por que: seção 14, o escravo entra no MESTRE pelo endereço dele e o mestre nunca é o
    # chamado a entrar, porque um grupo é formado nomeando quem o lidera.
    assert ("entrar_no_grupo", IP_1) in cozinha.chamadas
    assert not [c for c in sala.chamadas if c[0] == "entrar_no_grupo"]
    # Why: section 14, the volume of a slave goes through the master, never to the slave.
    # Por que: seção 14, o volume de um escravo passa pelo mestre, nunca vai para o escravo.
    await _ajustar(ws, NIVEL_2, 33, identificador=2)
    assert [q["ok"] for q in _acks(await _tudo(ws))] == [True]
    assert ("volume_de_escravo", (IP_2, 33)) in sala.chamadas
    assert ("volume", 33) not in cozinha.chamadas
    await _ajustar(ws, GRUPO, 0, identificador=3)
    quadros = await _tudo(ws)
    assert [q["ok"] for q in _acks(quadros)] == [True]
    assert [q["v"] for q in _reports(quadros, GRUPO)] == [0]
    assert ("desfazer_grupo", None) in sala.chamadas
    await ws.close()


async def test_um_quadro_ruim_e_recusado_e_o_socket_segue_vivo(hub):
    cliente = await hub()
    ws = await _abrir(cliente)
    ruins = (
        "nao e json",
        "[]",
        '{"t":"nada"}',
        '{"t":"set","dpid":"121","v":1}',
        _auth(),
    )
    for bruto in ruins:
        await ws.send_str(bruto)
        assert _acks(await _tudo(ws)) == [
            {"t": "ack", "id": None, "ok": False, "code": "frame_invalido"}
        ], bruto
    # Why: the other end is whatever bridge somebody implemented from the public contract, and
    # one bad frame must not drop a socket that is carrying a whole licence.
    # Por que: do outro lado está a ponte que alguém implementou do contrato público, e um
    # quadro ruim não pode derrubar um socket que carrega uma licença inteira.
    await _ajustar(ws, NIVEL_1, 30, identificador=9)
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 9, "ok": True, "code": None}]
    await ws.close()


async def test_um_dp_fora_do_contrato_e_recusado_com_o_codigo_estavel(hub):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _ajustar(ws, 999, 1)
    assert _acks(await _tudo(ws)) == [{"t": "ack", "id": 1, "ok": False, "code": "dp_desconhecido"}]
    await ws.close()


async def test_um_report_vai_para_todos_os_clientes_da_licenca(hub):
    cliente = await hub()
    primeiro = await _abrir(cliente)
    segundo = await _abrir(cliente)
    # A snapshot answered is an auth already taken, so both sockets are listening.
    # Um snapshot respondido é um auth já aceito, então os dois sockets estão escutando.
    await _consultar(primeiro)
    await _consultar(segundo)
    await _ajustar(primeiro, NIVEL_1, 33)
    assert _reports(await _tudo(primeiro), NIVEL_1)[0]["v"] == 33
    # Why: the slice of a licence is a broadcast, so a second bridge (or the panel of another
    # integrator) sees the same state and not a hub that answers only whoever commanded it.
    # Por que: a fatia de uma licença é difusão, então uma segunda ponte (ou o painel de outro
    # integrador) vê o mesmo estado e não um hub que responde só a quem o comandou.
    assert _reports(await _tudo(segundo), NIVEL_1)[0]["v"] == 33
    await primeiro.close()
    await segundo.close()


async def test_um_report_nao_atravessa_para_outra_licenca(hub, caixas, agenda):
    cliente = await hub(licencas=DUAS_LICENCAS)
    sala, cozinha = caixas.instancias
    cozinha._defina(volume=35)
    primeiro = await _abrir(cliente, AV)
    segundo = await _abrir(cliente, OUTRA)
    # Why: number 1 of each licence is another equipment, so the same data point answers with
    # the state of the licence that was named and never with the other one.
    # Por que: o número 1 de cada licença é outro equipamento, então o mesmo data point
    # responde com o estado da licença nomeada e nunca com o da outra.
    assert (await _consultar(primeiro))["dps"][str(NIVEL_1)] == 20
    assert (await _consultar(segundo))["dps"][str(NIVEL_1)] == 35
    await _assentar(agenda, primeiro, segundo)
    sala._defina(volume=61)
    assert await agenda.soltar(INTERVALO_S) == 1
    assert [q["v"] for q in _reports(await _tudo(primeiro), NIVEL_1)] == [61]
    assert await _tudo(segundo) == []
    await _ajustar(segundo, NIVEL_1, 44)
    quadros = await _tudo(segundo)
    assert _acks(quadros) == [OK]
    assert [q["v"] for q in _reports(quadros, NIVEL_1)] == [44]
    assert ("volume", 44) in cozinha.chamadas
    assert ("volume", 44) not in sala.chamadas
    assert await _tudo(primeiro) == []
    await primeiro.close()
    await segundo.close()


async def test_o_estado_que_o_aparelho_muda_sozinho_e_publicado_no_tique(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixas.instancias[0]._defina(volume=61)
    assert await agenda.soltar(INTERVALO_S) == 1
    assert _reports(await _tudo(ws), NIVEL_1) == [
        {"t": "report", "dpid": NIVEL_1, "v": 61, "ts": int(agenda())}
    ]
    # Why: a report is only ever born of real state, and a state that did not change is not a
    # report; a bus that republished everything on every tick would be noise on the platform.
    # Por que: um report só nasce de estado real, e um estado que não mudou não é report; um
    # barramento que republicasse tudo a cada tique seria ruído na plataforma.
    agenda.avancar(FOLGA_S)
    assert await agenda.soltar(INTERVALO_S) == 1
    assert await _tudo(ws) == []
    await ws.close()


async def test_a_janela_da_classe_a_segura_dois_segundos_e_o_ultimo_valor_vence(
    hub, caixas, agenda
):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    caixa._defina(volume=61)
    await agenda.soltar(INTERVALO_S)
    assert [q["v"] for q in _reports(await _tudo(ws), NIVEL_1)] == [61]
    # Why: section 8, a level that moves twice inside the window is one report and not two,
    # because a speaker being turned by hand would otherwise cost the day a report per tick.
    # Por que: seção 8, um nível que se move duas vezes dentro da janela é um report e não
    # dois, porque uma caixa girada na mão custaria ao dia um report por tique.
    caixa._defina(volume=62)
    agenda.avancar(1.0)
    await agenda.soltar(INTERVALO_S)
    assert await _tudo(ws) == []
    caixa._defina(volume=63)
    agenda.avancar(0.5)
    await agenda.soltar(INTERVALO_S)
    assert await _tudo(ws) == []
    # Why: the reading that was held is NOT lost, it is published on the first tick after the
    # window, and it is the LAST value, because 62 is a level the speaker no longer has.
    # Por que: a leitura segurada NÃO se perde, ela sai no primeiro tique depois da janela, e
    # é o ÚLTIMO valor, porque 62 é um nível que a caixa não tem mais.
    agenda.avancar(JANELA_A_S - 1.5)
    await agenda.soltar(INTERVALO_S)
    assert [q["v"] for q in _reports(await _tudo(ws), NIVEL_1)] == [63]
    await ws.close()


async def test_a_janela_da_classe_b_segura_dez_segundos(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    caixa._defina(fonte="line-in", mudo=True)
    await agenda.soltar(INTERVALO_S)
    reportados = _reportados(await _tudo(ws))
    assert reportados[ENTRADAS] == "1=2;2=1"
    assert reportados[MUDOS] == 0b1
    # Why: section 8, inputs and muted are context and not state the app must see now, so
    # they wait longer than the level and the power, which are the two things the customer
    # is looking at with the app open.
    # Por que: seção 8, entradas e mudos são contexto e não estado que o app precisa ver
    # agora, então esperam mais que o nível e o ligado, que são as duas coisas que o cliente
    # está olhando com o app aberto.
    caixa._defina(fonte="wifi", mudo=False)
    agenda.avancar(JANELA_A_S + 1.0)
    await agenda.soltar(INTERVALO_S)
    assert await _tudo(ws) == []
    agenda.avancar(JANELA_B_S - JANELA_A_S - 1.0)
    await agenda.soltar(INTERVALO_S)
    reportados = _reportados(await _tudo(ws))
    assert reportados[ENTRADAS] == "1=1;2=1"
    assert reportados[MUDOS] == 0
    await ws.close()


async def test_o_titulo_nunca_e_empurrado_e_responde_a_consulta(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    caixa._defina(tocando="Primeira")
    await agenda.soltar(INTERVALO_S)
    # Why: section 8, a title changes with every track and pushing it would be the one second
    # sensor of this product; it answers the consulta of the panel and is never pushed.
    # Por que: seção 8, um título muda a cada faixa e empurrá-lo seria o sensor de um segundo
    # deste produto; ele responde à consulta do painel e nunca é empurrado.
    assert await _tudo(ws) == []
    assert (await _consultar(ws))["dps"][str(TITULOS)] == "1=Primeira"
    caixa._defina(tocando="Segunda")
    agenda.avancar(FOLGA_S)
    await agenda.soltar(INTERVALO_S)
    assert await _tudo(ws) == []
    assert (await _consultar(ws))["dps"][str(TITULOS)] == "1=Segunda"
    await ws.close()


async def test_os_nomes_saem_na_hora_quando_o_cadastro_muda(hub, caixas, agenda):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    caixa = caixas.instancias[0]
    cliente.app[CENAS].trocar((Cena(nome="Noite"),))
    caixa._defina(volume=61)
    await agenda.soltar(INTERVALO_S)
    reportados = _reportados(await _tudo(ws))
    assert json.loads(reportados[NOMES_CENAS_1]) == {"c": ["Noite"]}
    assert reportados[NIVEL_1] == 61
    # Why: section 8, the names and the profiles only move when the registration moves, so
    # they carry no window at all: the integrator who just saved wants to see it on the app
    # now, while the level that moved in the same second still waits its window.
    # Por que: seção 8, os nomes e os perfis só se movem quando o cadastro se move, então não
    # carregam janela nenhuma: o integrador que acabou de salvar quer ver no app agora,
    # enquanto o nível que se moveu no mesmo segundo ainda espera a janela dele.
    cliente.app[CENAS].trocar((Cena(nome="Noite"), Cena(nome="Dia")))
    caixa._defina(volume=62)
    await agenda.soltar(INTERVALO_S)
    reportados = _reportados(await _tudo(ws))
    assert json.loads(reportados[NOMES_CENAS_1]) == {"c": ["Noite", "Dia"]}
    assert NIVEL_1 not in reportados
    await ws.close()


async def test_um_numero_esvaziado_corrige_o_que_ja_tinha_reportado(hub, agenda):
    """Section 8: a number whose equipment was removed stops producing values, and the last
    thing published about it must not stand forever; when the number is occupied again its
    state is new and reports again.

    Seção 8: um número cujo equipamento foi removido para de produzir valores, e o último
    publicado a respeito dele não pode ficar valendo para sempre; quando o número é ocupado de
    novo o estado dele é novo e reporta de novo.
    """
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    livro = cliente.app[LICENCAS]
    await livro.esquecer("uuid-1")
    await cliente.app[BARRAMENTO].publicar()
    reportados = _reportados(await _tudo(ws))
    assert reportados[ONLINE] == 0b10
    assert reportados[ENTRADAS] == "2=1"
    assert reportados[PERFIS_1] == "2|au|Cozinha|Wi-Fi,Linha|||NMEPG"
    # Why: the level of an empty number is no level at all, so it is absent instead of a zero
    # a bridge would take for a speaker turned all the way down.
    # Por que: o nível de um número vazio não é nível nenhum, então ele fica ausente em vez de
    # um zero que a ponte tomaria por uma caixa toda abaixada.
    assert NIVEL_1 not in reportados
    assert str(NIVEL_1) not in (await _consultar(ws))["dps"]
    await livro.definir_ordem(AV, ["uuid-1", "uuid-2"])
    agenda.avancar(FOLGA_S)
    await cliente.app[BARRAMENTO].publicar()
    reportados = _reportados(await _tudo(ws))
    assert reportados[NIVEL_1] == 20
    assert reportados[ONLINE] == 0b11
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


async def test_um_driver_que_recusa_vira_codigo_estavel_e_nao_reporta(hub, caixas):
    cliente = await hub()
    ws = await _abrir(cliente)
    caixas.instancias[0].recusa = "erro_aparelho"
    await _ajustar(ws, NIVEL_1, 50)
    # Why: a speaker that refused is not a state to publish, so the ack carries the code of
    # section 6 and no report goes out claiming the level changed.
    # Por que: uma caixa que recusou não é estado para publicar, então o ack leva o código da
    # seção 6 e nenhum report sai dizendo que o nível mudou.
    quadros = await _tudo(ws)
    assert _acks(quadros) == [{"t": "ack", "id": 1, "ok": False, "code": "erro_aparelho"}]
    assert _reports(quadros, NIVEL_1) == []
    await ws.close()


async def test_um_defeito_nosso_vira_codigo_estavel_e_nunca_uma_excecao():
    async def explode(licenca: str, dpid: object, valor: object) -> str | None:
        raise RuntimeError("quebrei")

    # Why: an exception out of here would leave the client waiting for an ack that never comes
    # and would take down a socket that is carrying a whole licence.
    # Por que: uma exceção saindo daqui deixaria o cliente esperando por um ack que nunca vem e
    # derrubaria um socket que carrega uma licença inteira.
    barramento = Barramento(explode, lambda licenca: {}, lambda: TOKEN, lambda licenca: "av")
    assert await barramento.aplicar(AV, NIVEL_1, 10) == "erro_interno"


async def test_um_cliente_que_para_de_ler_e_descartado(hub):
    """A bridge that stops reading must not freeze the single task that publishes every
    report and reconciles the groups of every licence.

    Why: send_str waits for the kernel buffer, so one stalled socket held the publish loop of
    the whole hub for everybody, and the frames it never took grew without bound in the daemon
    of an appliance. A socket that does not take a frame within the deadline is dropped.

    Uma ponte que para de ler não pode congelar a única tarefa que publica todo report e
    reconcilia os grupos de toda licença.

    Por que: o send_str espera pelo buffer do kernel, então um socket travado segurava o laço
    de publicação do hub inteiro para todo mundo, e os quadros que ele nunca pegou cresciam
    sem limite no daemon de um appliance. Um socket que não pega um quadro dentro do prazo é
    descartado.
    """
    cliente = await hub()
    barramento = cliente.app[BARRAMENTO]
    barramento._envio_s = 0.2
    travado = _Travado()
    barramento._canal(AV).clientes.add(travado)
    assert barramento.ouvintes_de(AV) == 1
    laco = asyncio.get_running_loop()
    comeco = laco.time()
    await barramento.publicar()
    gasto = laco.time() - comeco
    # Bounded by the deadline and a little slack, and not by the client, which never reads.
    # Limitado pelo prazo e uma folga, e não pelo cliente, que nunca lê.
    assert gasto < 2.0, f"the publish loop waited {gasto:.1f}s on one client that stopped reading"
    assert barramento.ouvintes_de(AV) == 0
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


async def test_o_dp_de_ligado_nao_ganha_um_false_inventado_na_releitura(fabrica_cliente, agenda):
    """Section 8: a report is only ever born of real state. A TV in a number has DP 101 as
    its power switch; when its driver cannot say whether it is on, the optimistic report of
    a set stands and the reread publishes nothing, instead of a False the bus made up
    because the value was absent.

    Seção 8: um report só nasce de estado real. Uma TV num número tem o DP 101 como chave de
    ligar; quando o driver dela não sabe dizer se está ligada, o report otimista de um set
    fica de pé e a releitura não publica nada, em vez de um False que o barramento inventou
    porque o valor estava ausente.
    """
    tipo = "tv_falsa"

    class Tv(Driver):
        MANIFESTO = Manifesto(
            tipo=tipo,
            rotulo={"pt": "TV", "en": "TV"},
            categoria="tv",
            capacidades=("ligar", "desligar"),
            textos=_textos("TV de teste"),
        )
        chamadas: list[tuple[str, object]] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self._defina(online=True)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            type(self).chamadas.append((acao, valor))
            return None

    config = Config(
        equipamentos=(Cadastro(identidade="uuid-tv", tipo=tipo, nome="TV", ip=IP_1),),
        licencas=(Licenca(id=AV, produto="av"),),
        numeros={AV: ("uuid-tv",)},
    )
    cliente = await fabrica_cliente(
        config=config,
        segredos=Segredos(api_token=TOKEN),
        catalogo={tipo: Tv},
        dormir=agenda.dormir,
        agora=agenda,
    )
    ws = await _abrir(cliente)
    dps = (await _consultar(ws))["dps"]
    assert str(LIGADO_1) not in dps
    assert dps[str(PERFIS_1)] == "1|tv|TV||||L"
    await _ajustar(ws, LIGADO_1, True)
    assert [q["v"] for q in _reports(await _tudo(ws), LIGADO_1)] == [True]
    assert Tv.chamadas == [("ligar", None)]
    agenda.avancar(JANELA_A_S)
    assert await agenda.soltar(RELEITURA_S) == 1
    assert _reports(await _tudo(ws), LIGADO_1) == []
    await ws.close()


async def test_em_250_reports_a_classe_b_para_e_a_classe_a_espera_30_s(hub, caixas, agenda, caplog):
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    barramento = cliente.app[BARRAMENTO]
    caixa = caixas.instancias[0]
    volume = 30
    with caplog.at_level(logging.WARNING, logger="iphub.dpbus.socket"):
        # A level that moves on every tick, with the window of section 8 respected each time,
        # is the cheapest way to spend the day of a licence.
        # Um nível que se move a cada tique, com a janela da seção 8 respeitada de cada vez, é
        # o jeito mais barato de gastar o dia de uma licença.
        while barramento.reports_do_dia(AV) < AVISO_DO_DIA:
            volume = 31 if volume == 30 else 30
            caixa._defina(volume=volume)
            agenda.avancar(JANELA_A_S)
            assert await agenda.soltar(INTERVALO_S) == 1
    assert barramento.reports_do_dia(AV) == AVISO_DO_DIA
    # Why: section 8, the operator reads in the log why the app went quiet on inputs and modes.
    # Por que: seção 8, o operador lê no log por que o app emudeceu em entradas e modos.
    assert f"{AVISO_DO_DIA} reports" in caplog.text
    assert f"{int(JANELA_APERTADA_S)} s" in caplog.text
    await _tudo(ws)
    caixa._defina(volume=50, mudo=True)
    agenda.avancar(JANELA_B_S)
    await agenda.soltar(INTERVALO_S)
    # Why: past the warning the muted bits (class B) stop and the level (class A) waits the
    # widened window, so the cloud never gets to throttle the device.
    # Por que: depois do aviso os bits de mudo (classe B) param e o nível (classe A) espera a
    # janela alargada, então a nuvem nunca chega a limitar o dispositivo.
    assert await _tudo(ws) == []
    agenda.avancar(JANELA_APERTADA_S - JANELA_B_S)
    await agenda.soltar(INTERVALO_S)
    reportados = _reportados(await _tudo(ws))
    assert reportados == {NIVEL_1: 50}
    assert barramento.reports_do_dia(AV) == AVISO_DO_DIA + 1
    # Why: the day turns on the clock, the count starts over and what class B was holding
    # goes out on the first tick of the new day.
    # Por que: o dia vira pelo relógio, a contagem recomeça e o que a classe B segurava sai no
    # primeiro tique do dia novo.
    agenda.avancar(SEGUNDOS_POR_DIA)
    await agenda.soltar(INTERVALO_S)
    assert _reportados(await _tudo(ws)) == {MUDOS: 0b1}
    assert barramento.reports_do_dia(AV) == 1
    await ws.close()


async def test_a_virada_do_dia_zera_a_contagem(hub, agenda):
    cliente = await hub()
    barramento = cliente.app[BARRAMENTO]
    # Why: a report nobody listens to never reaches the cloud, so the day only counts once a
    # bridge is on the socket.
    # Por que: um report que ninguém escuta nunca chega à nuvem, então o dia só conta depois de
    # uma ponte estar no socket.
    assert await agenda.soltar(INTERVALO_S) == 1
    assert barramento.reports_do_dia(AV) == 0
    ws = await _abrir(cliente)
    await _ajustar(ws, NIVEL_2, 33)
    await _tudo(ws)
    assert barramento.reports_do_dia(AV) > 0
    await ws.close()
    agenda.avancar(SEGUNDOS_POR_DIA)
    # Why: section 8 counts reports per day, and the platform counts them by its own clock,
    # so the count of yesterday must not tighten the windows of today.
    # Por que: a seção 8 conta reports por dia, e a plataforma os conta pelo relógio dela,
    # então a contagem de ontem não pode apertar as janelas de hoje.
    assert barramento.reports_do_dia(AV) == 0


async def test_o_produto_ar_publica_e_ajusta_a_maquina_1_em_101_a_104(hub, ares):
    cliente = await hub(licencas=COM_AR)
    ws = await _abrir(cliente, AR)
    dps = (await _consultar(ws))["dps"]
    assert dps[str(AR_LIGADO_1)] is False
    assert dps[str(AR_TEMPERATURA_1)] == 24
    assert dps[str(AR_MODO_1)] == "frio"
    assert dps[str(AR_VENTO_1)] == "auto"
    assert dps[str(AR_ONLINE)] == 0b1
    assert json.loads(dps[str(AR_NOMES)]) == {"m": ["Quarto"]}
    assert str(CENA_AR) not in dps
    # Why: the slice of a licence of air is the table of air, so nothing of the audio and
    # video product (its online bits, its group) is ever in it.
    # Por que: a fatia de uma licença de ar é a tabela de ar, então nada do produto de áudio e
    # vídeo (os bits de online dele, o grupo dele) está nela.
    assert str(ONLINE) not in dps
    assert str(GRUPO) not in dps
    maquina = ares.instancias[0]
    pedidos = (
        (AR_LIGADO_1, True),
        (AR_TEMPERATURA_1, 22),
        (AR_MODO_1, "quente"),
        (AR_VENTO_1, "alto"),
    )
    for identificador, (dpid, valor) in enumerate(pedidos, start=1):
        await _ajustar(ws, dpid, valor, identificador)
        quadros = await _tudo(ws)
        assert [q["v"] for q in _reports(quadros, dpid)] == [valor]
        assert _acks(quadros) == [{"t": "ack", "id": identificador, "ok": True, "code": None}]
    assert maquina.chamadas == [
        ("ligar", None),
        ("temperatura", 22),
        ("modo", "quente"),
        ("vento", "alto"),
    ]
    # Why: section 8, the setpoint is whole degrees from 16 to 30 and the mode is one of five
    # words; anything else is refused by the contract before any driver hears of it.
    # Por que: seção 8, o setpoint são graus inteiros de 16 a 30 e o modo é uma de cinco
    # palavras; o resto é recusado pelo contrato antes de qualquer driver saber.
    await _ajustar(ws, AR_TEMPERATURA_1, 35, identificador=9)
    await _ajustar(ws, AR_MODO_1, "turbo", identificador=10)
    assert [(q["id"], q["code"]) for q in _acks(await _tudo(ws))] == [
        (9, "valor_invalido"),
        (10, "valor_invalido"),
    ]
    assert len(maquina.chamadas) == 4
    await ws.close()


async def test_remover_uma_licenca_fecha_os_sockets_dela_e_deixa_as_outras(hub, posse, bearer):
    cliente = await hub(licencas=DUAS_LICENCAS)
    sessao = await posse(cliente)
    # Why: taking ownership rotates the machine credential, so the sockets authenticate with
    # the token the hub holds now.
    # Por que: tomar posse rotaciona a credencial de máquina, então os sockets autenticam com
    # o token que o hub tem agora.
    token = cliente.app[SEGREDOS].valor.api_token
    ws = await _abrir(cliente, AV, token)
    outro = await _abrir(cliente, OUTRA, token)
    await _consultar(ws)
    await _consultar(outro)
    barramento = cliente.app[BARRAMENTO]
    assert (barramento.ouvintes_de(AV), barramento.ouvintes_de(OUTRA)) == (1, 1)
    resposta = await cliente.delete(f"/api/licencas/{OUTRA}", headers=bearer(sessao))
    assert resposta.status == 200, await resposta.text()
    # Why: a licence that left the installation is a device the platform no longer has, so
    # the bridge of that device is closed instead of being kept talking to numbers that are
    # gone; the other licences never notice.
    # Por que: uma licença que saiu da instalação é um dispositivo que a plataforma não tem
    # mais, então a ponte daquele dispositivo é fechada em vez de seguir falando com números
    # que se foram; as outras licenças nem percebem.
    await _ate_fechar(outro)
    assert barramento.ouvintes_de(OUTRA) == 0
    assert barramento.ouvintes_de(AV) == 1
    await _ajustar(ws, NIVEL_1, 30)
    assert _acks(await _tudo(ws)) == [OK]
    await ws.close()


async def test_um_set_do_valor_ja_publicado_nao_repete_o_report(hub, agenda):
    """Section 8, the bus reports only what changed and never repeats: an automation that
    writes the level the equipment already has costs no report of the day.

    Seção 8, o barramento reporta só o que mudou e nunca repete: uma automação que escreve o
    nível que o equipamento já tem não custa report do dia.
    """
    cliente = await hub()
    ws = await _abrir(cliente)
    await _assentar(agenda, ws)
    barramento = cliente.app[BARRAMENTO]
    nivel_1 = mapa.dp_de(mapa.PRODUTO_AV, "nivel", 1)
    await _ajustar(ws, nivel_1, 20)
    quadros = await _tudo(ws)
    assert [q["t"] for q in quadros] == ["ack"]
    assert barramento.reports_do_dia(AV) == 0
    await _ajustar(ws, nivel_1, 21)
    quadros = await _tudo(ws)
    assert _reports(quadros, nivel_1) == [
        {"t": "report", "dpid": nivel_1, "v": 21, "ts": int(agenda())}
    ]
    assert barramento.reports_do_dia(AV) == 1
    await ws.close()
