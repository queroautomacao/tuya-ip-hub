# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the scene routes: data in, a stable code out, and one run at a time.

A scene is DATA, so every refusal here is the validation of section 8 answering by field,
and every acceptance is a list that reached the disk before it reached the executor. The
steps run on a task of their own, which is what the run route promises, so the tests wait
for the driver to be reached instead of for the request to come back.

A step runs through the book of licences: an equipment that holds a number of a licence of
audio and video is routed through the group of that licence, and one that holds no number
goes straight to the gestor. The fakes here carry the group moves of section 14, so a scene
can form a group and a test can prove where every step landed. The same scene is fired by
its route, by the scene data point of a licence of audio and video and by the one of a
licence of air, because the number is the same scene on every licence.

The executor sleeps with the clock of the process, so every step here names a wait of zero
and the one test that proves a wait separates two steps spends fifty milliseconds of it.

The data point numbers are written by hand in this file. A test that asked the map for them
would agree with any change the map made to the contract of section 8, which is exactly what
a contract test exists to catch.

O contrato das rotas de cena: dado entra, código estável sai, e uma execução por vez.

Uma cena é DADO, então toda recusa aqui é a validação da seção 8 respondendo por campo, e
toda aceitação é uma lista que chegou ao disco antes de chegar ao executor. Os passos correm
numa tarefa própria, que é o que a rota de execução promete, então os testes esperam o driver
ser alcançado e não a requisição voltar.

Um passo corre pelo livro de licenças: um equipamento que ocupa um número de uma licença de
áudio e vídeo é roteado pelo grupo daquela licença, e um que não ocupa número vai direto ao
gestor. Os falsos daqui carregam os movimentos de grupo da seção 14, então uma cena consegue
formar um grupo e um teste consegue provar onde cada passo caiu. A mesma cena é disparada
pela rota dela, pelo data point de cena de uma licença de áudio e vídeo e pelo de uma licença
de ar, porque o número é a mesma cena em toda licença.

O executor dorme com o relógio do processo, então todo passo daqui nomeia uma espera zero e o
único teste que prova que uma espera separa dois passos gasta cinquenta milissegundos dela.

Os números de data point são escritos na mão neste arquivo. Um teste que os pedisse ao mapa
concordaria com qualquer mudança que o mapa fizesse no contrato da seção 8, que é exatamente
o que um teste de contrato existe para pegar.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

import pytest

from iphub.api.cenas import CORPO_MAXIMO_CENAS
from iphub.api.comum import CORPO_MAXIMO, config_de
from iphub.cenas import Cena, Passo
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config, ConfigIncompativel, Licenca
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto
from iphub.versao import SCHEMA_VERSION

TIPO = "multiroom_falso"
TIPO_DE_AR = "ar_falso"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")
CAPACIDADES_DE_AR = ("ligar", "desligar", "temperatura", "modo", "vento")
MODOS = ("frio", "quente")
VENTOS = ("auto", "alto")

IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"
IP_AR = "192.0.2.21"

# The numbers of section 8, written by hand on purpose: the scene data point and the two
# name data points of each product.
# Os números da seção 8, escritos na mão de propósito: o data point de cena e os dois de
# nomes de cada produto.
CENA_AV, NOMES_AV_1, NOMES_AV_2 = 141, 154, 155
CENA_AR, NOMES_AR_1, NOMES_AR_2 = 171, 174, 175

# The ceilings of section 8, and the actions of a scene as the panel reads them: the
# capabilities of section 6 without agrupar, which only a manifest declares, plus grupo,
# which is the move itself.
# Os tetos da seção 8, e as ações de uma cena como o painel as lê: as capacidades da seção 6
# sem agrupar, que só um manifesto declara, mais grupo, que é o movimento em si.
MAXIMO = 32
PASSOS_MAXIMOS = 64
ESPERA_MAXIMA_MS = 30_000
INTERVALO_PADRAO_MS = 1_000
ACOES = [
    "ligar",
    "desligar",
    "volume",
    "mudo",
    "fonte",
    "tocar",
    "pausar",
    "proxima",
    "anterior",
    "tecla",
    "atalho",
    "modo",
    "vento",
    "temperatura",
    "comando_extra",
    "grupo",
]

COM_SESSAO = [("GET", "/api/cenas"), ("POST", "/api/cenas"), ("POST", "/api/cenas/1/executar")]

# Why: the deadline is the whole point of a test that waits, and a scene reaches a fake
# driver in microseconds; anything longer than this is a scene that never started.
# Por que: o prazo é o ponto de um teste que espera, e uma cena alcança um driver falso em
# microssegundos; algo maior que isto é uma cena que nunca começou.
PRAZO_S = 2.0

# Why: long enough for the check between the two steps to land inside the wait on any
# machine, short enough not to be felt in the suite.
# Por que: longo o bastante para a conferência entre os dois passos cair dentro da espera em
# qualquer máquina, curto o bastante para não ser sentido na suíte.
ESPERA_REAL_MS = 50


@dataclass(frozen=True)
class _Grupo:
    """What a master answers when it is asked which speakers follow it.

    O que um mestre responde quando lhe perguntam que caixas o seguem.
    """

    escravos: tuple = ()


def _manifesto(
    tipo: str, categoria: str, capacidades: tuple[str, ...], **palavras: tuple[str, ...]
) -> Manifesto:
    textos = {"descricao": "Aparelho de teste"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Aparelho", "en": "Device"},
        categoria=categoria,
        capacidades=capacidades,
        textos={"pt": dict(textos), "en": dict(textos)},
        **palavras,
    )


def _fabrica(
    tipo: str = TIPO,
    *,
    categoria: str = "multiroom",
    capacidades: tuple[str, ...] = CAPACIDADES,
    eventos: list[str] | None = None,
    **palavras: tuple[str, ...],
) -> type[Driver]:
    """A driver that records what reached it, with the group moves of section 14 and knobs,
    so a test proves what reached each equipment, in which order, and breaks one on purpose.

    Um driver que guarda o que chegou nele, com os movimentos de grupo da seção 14 e botões,
    para um teste provar o que chegou a cada equipamento, em que ordem, e quebrar um de
    propósito.
    """
    registro: list[str] = [] if eventos is None else eventos

    class Falso(Driver):
        MANIFESTO = _manifesto(tipo, categoria, capacidades, **palavras)
        instancias: list["Falso"] = []
        eventos = registro

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self.pausa: asyncio.Event | None = None
            self.recusa: str | None = None
            self.estoura = False
            self.grupo = _Grupo()
            self._defina(online=True, ligado=True, volume=20)
            type(self).instancias.append(self)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            return await self._passo(acao, valor)

        async def _passo(self, nome: str, valor: object) -> str | None:
            self.chamadas.append((nome, valor))
            registro.append(f"{self.cadastro.identidade}:{nome}")
            if self.pausa is not None:
                await self.pausa.wait()
            if self.estoura:
                raise RuntimeError("quebrei")
            return self.recusa

        async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
            return await self._passo("entrar_no_grupo", ip_do_mestre)

        async def desfazer_grupo(self) -> str | None:
            return await self._passo("desfazer_grupo", None)

        async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
            return await self._passo("volume_de_escravo", (ip, valor))

        async def ler_grupo(self) -> _Grupo:
            return self.grupo

        def marcar_grupo(self, dentro: bool) -> None:
            self.no_grupo = dentro

        def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
            self._defina(tocando=tocando, reproduzindo=reproduzindo)

        def e_escravo(self) -> bool:
            return False

        def saiu_do_grupo(self) -> bool:
            return False

    Falso.instancias = []
    return Falso


def _catalogo() -> dict[str, type[Driver]]:
    """Two kinds of fake sharing one event log: a multiroom speaker and an air conditioner.

    Dois tipos de falso dividindo um registro de eventos: uma caixa multiroom e um ar
    condicionado.
    """
    eventos: list[str] = []
    return {
        TIPO: _fabrica(eventos=eventos),
        TIPO_DE_AR: _fabrica(
            TIPO_DE_AR,
            categoria="ar_condicionado",
            capacidades=CAPACIDADES_DE_AR,
            eventos=eventos,
            modos=MODOS,
            ventos=VENTOS,
        ),
    }


def _cadastro(identidade: str, tipo: str = TIPO, ip: str = IP_1, nome: str = "Sala") -> Cadastro:
    return Cadastro(identidade=identidade, tipo=tipo, nome=nome, ip=ip)


def _equipamentos() -> tuple[Cadastro, ...]:
    return (
        _cadastro("uuid-1", ip=IP_1, nome="Sala"),
        _cadastro("uuid-2", ip=IP_2, nome="Cozinha"),
        _cadastro("uuid-ar", tipo=TIPO_DE_AR, ip=IP_AR, nome="Quarto"),
    )


def _aparelho(catalogo: dict, identidade: str):
    return next(
        aparelho
        for classe in catalogo.values()
        for aparelho in classe.instancias
        if aparelho.cadastro.identidade == identidade
    )


def _eventos(catalogo: dict) -> list[str]:
    return catalogo[TIPO].eventos


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer, agenda):
    """A hub with the catalog, the registrations, the licences and the scenes the test wants,
    already owned, with the clock of the bus in the hand of the test.

    Um hub com o catálogo, os cadastros, as licenças e as cenas que o teste quiser, já com
    dono, com o relógio do barramento na mão do teste.
    """

    async def criar(catalogo: dict, *, equipamentos=(), licencas=(), numeros=None, cenas=()):
        cliente = await fabrica_cliente(
            catalogo=catalogo,
            config=Config(
                equipamentos=equipamentos,
                licencas=licencas,
                numeros={} if numeros is None else numeros,
                cenas=cenas,
            ),
            dormir=agenda.dormir,
            agora=agenda,
        )
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def hub(abrir):
    """Two speakers and an air conditioner registered, no licence and no scene saved yet.

    Duas caixas e um ar condicionado cadastrados, nenhuma licença e nenhuma cena salva ainda.
    """
    catalogo = _catalogo()
    cliente, auth = await abrir(catalogo, equipamentos=_equipamentos())
    return cliente, auth, catalogo


@pytest.fixture
async def licenciado(abrir):
    """The same hub with a licence of audio and video created by its route and the two
    speakers in numbers 1 and 2 of it.

    O mesmo hub com uma licença de áudio e vídeo criada pela rota dela e as duas caixas nos
    números 1 e 2 dela.
    """
    catalogo = _catalogo()
    cliente, auth = await abrir(catalogo, equipamentos=_equipamentos())
    assert await _licenca(cliente, auth, "av") == "av1"
    resposta = await cliente.post(
        "/api/licencas/av1/numeros", json={"numeros": ["uuid-1", "uuid-2"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    return cliente, auth, catalogo


async def _json(resposta) -> dict:
    return await resposta.json()


async def _esperar(condicao) -> None:
    """Waits for the task of the scene to reach the driver, with a deadline.

    Espera a tarefa da cena alcançar o driver, com prazo.
    """
    async with asyncio.timeout(PRAZO_S):
        while not condicao():
            await asyncio.sleep(0)


async def _ate_terminar(cliente, auth, numero: int = 1) -> None:
    """Waits for the run of one scene to end, as the listing tells it.

    Espera a execução de uma cena acabar, como a listagem conta.
    """
    async with asyncio.timeout(PRAZO_S):
        while (await _json(await cliente.get("/api/cenas", headers=auth)))["cenas"][numero - 1][
            "em_curso"
        ]:
            await asyncio.sleep(0)


def _passo(
    equipamento: str = "uuid-1", acao: str = "volume", valor: object = 30, espera_ms: object = 0
) -> dict:
    return {"equipamento": equipamento, "acao": acao, "valor": valor, "espera_ms": espera_ms}


def _cena(nome: str = "Filme", passos=None) -> dict:
    return {"nome": nome, "passos": [_passo()] if passos is None else list(passos)}


async def _salvar(cliente, auth, cenas: list[dict]):
    return await cliente.post("/api/cenas", json={"cenas": cenas}, headers=auth)


async def _executar(cliente, auth, numero: object = 1):
    return await cliente.post(f"/api/cenas/{numero}/executar", headers=auth)


async def _listar(cliente, auth) -> list[dict]:
    return (await _json(await cliente.get("/api/cenas", headers=auth)))["cenas"]


def _problemas(corpo: dict) -> list[tuple[str, str]]:
    return [(problema["campo"], problema["codigo"]) for problema in corpo["problemas"]]


async def _licenca(cliente, auth, produto: str) -> str:
    resposta = await cliente.post("/api/licencas", json={"produto": produto}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    return (await _json(resposta))["licenca"]["id"]


async def _licenca_json(cliente, auth, id_licenca: str) -> dict:
    corpo = await _json(await cliente.get("/api/licencas", headers=auth))
    return next(licenca for licenca in corpo["licencas"] if licenca["id"] == id_licenca)


async def _dp(cliente, auth, id_licenca: str, dpid: int, valor: object):
    return await cliente.post(
        f"/api/licencas/{id_licenca}/dp/{dpid}", json={"v": valor}, headers=auth
    )


async def _dps(cliente, auth, id_licenca: str) -> dict:
    return (await _json(await cliente.get(f"/api/licencas/{id_licenca}/dps", headers=auth)))["dps"]


def _gravar_config(amb, **chaves: object) -> None:
    """A config.json written by hand, which is the other door into the scenes.

    Um config.json escrito na mão, que é a outra porta para as cenas.
    """
    amb.dir_data.mkdir(parents=True, exist_ok=True)
    (amb.dir_data / ARQUIVO_CONFIG).write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, **chaves}), encoding="utf-8"
    )


@pytest.mark.parametrize(("metodo", "caminho"), COM_SESSAO)
async def test_as_rotas_de_cena_exigem_sessao(cliente, metodo, caminho):
    """A scene reaches every equipment of the house, so nobody runs or rewrites one
    without a session.

    Uma cena alcança todo equipamento da casa, então ninguém roda ou reescreve uma sem sessão.
    """
    resposta = await cliente.request(metodo, caminho)
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "nao_autenticado"}


async def test_um_hub_sem_cena_responde_a_lista_vazia_e_os_tetos(hub):
    """Section 8: thirty two scenes, and the panel reads the ceilings and the actions from
    here instead of carrying a copy of the contract.

    Seção 8: trinta e duas cenas, e o painel lê os tetos e as ações daqui em vez de carregar
    uma cópia do contrato.
    """
    cliente, auth, _catalogo = hub
    corpo = await _json(await cliente.get("/api/cenas", headers=auth))
    assert corpo["ok"] is True
    assert corpo["cenas"] == []
    assert corpo["maximo"] == MAXIMO
    assert corpo["passos_maximos"] == PASSOS_MAXIMOS
    assert corpo["espera_maxima_ms"] == ESPERA_MAXIMA_MS
    assert corpo["intervalo_padrao_ms"] == INTERVALO_PADRAO_MS
    assert corpo["acoes"] == ACOES


async def test_a_cena_salva_chega_ao_arquivo_e_a_leitura(hub, amb):
    """A scene that lived only in memory would be gone on the next boot, in silence, and what
    the file holds is the same dataclass the route saved.

    Uma cena que vivesse só na memória sumiria no próximo boot, em silêncio, e o que o arquivo
    guarda é a mesma dataclass que a rota salvou.
    """
    cliente, auth, _catalogo = hub
    passos = (
        _passo("uuid-1", "volume", 30, espera_ms=250),
        _passo("uuid-2", "tocar", None, espera_ms=0),
    )
    resposta = await _salvar(cliente, auth, [_cena(passos=passos)])
    assert resposta.status == 200, await resposta.text()
    em_disco = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    assert em_disco["cenas"] == [
        {
            "nome": "Filme",
            "intervalo_ms": 1000,
            "passos": [
                {"equipamento": "uuid-1", "acao": "volume", "valor": 30, "espera_ms": 250},
                {"equipamento": "uuid-2", "acao": "tocar", "valor": None, "espera_ms": 0},
            ],
        }
    ]
    assert config_de(cliente.app).cenas == (
        Cena(
            nome="Filme",
            passos=(
                Passo(equipamento="uuid-1", acao="volume", valor=30, espera_ms=250),
                Passo(equipamento="uuid-2", acao="tocar", valor=None, espera_ms=0),
            ),
        ),
    )
    cenas = await _listar(cliente, auth)
    assert cenas[0]["numero"] == 1
    assert cenas[0]["nome"] == "Filme"
    assert cenas[0]["em_curso"] is False
    assert cenas[0]["passos"] == list(passos)


async def test_a_vaga_de_uma_cena_apagada_continua_ali(hub):
    """The POSITION is the number, so erasing scene 1 must not move scene 2 into it.

    A POSIÇÃO é o número, então apagar a cena 1 não pode mover a cena 2 para ela.
    """
    cliente, auth, _catalogo = hub
    assert (await _salvar(cliente, auth, [_cena("Filme"), _cena("Festa")])).status == 200
    assert (
        await _salvar(cliente, auth, [{"nome": "", "passos": []}, _cena("Festa")])
    ).status == 200
    cenas = await _listar(cliente, auth)
    assert [cena["nome"] for cena in cenas] == ["", "Festa"]
    assert cenas[1]["numero"] == 2


@pytest.mark.parametrize(
    ("equipamento", "codigo"),
    [
        ("uuid-9", "cena_equipamento_desconhecido"),
        ("", "cena_equipamento_invalido"),
        (7, "cena_equipamento_invalido"),
    ],
)
async def test_um_passo_sobre_quem_ninguem_cadastrou_e_recusado(hub, equipamento, codigo):
    """A scene saved over an identity nobody registered is a button that never does anything,
    and the integrator is at the keyboard right now to fix it.

    Uma cena salva sobre uma identidade que ninguém cadastrou é um botão que nunca faz nada, e
    o integrador está no teclado agora para consertar.
    """
    cliente, auth, _catalogo = hub
    resposta = await _salvar(cliente, auth, [_cena(passos=(_passo(equipamento),))])
    assert resposta.status == 400
    corpo = await _json(resposta)
    assert corpo["code"] == "cenas_invalidas"
    assert _problemas(corpo) == [("cenas[0].passos[0].equipamento", codigo)]
    assert await _listar(cliente, auth) == []


@pytest.mark.parametrize("acao", ["explodir", "agrupar", "cena", 3, None])
async def test_uma_acao_fora_do_vocabulario_e_recusada(hub, acao):
    """The vocabulary is the one of section 6 plus grupo: agrupar is what a manifest declares
    and never a step, and there is no step that runs a scene.

    O vocabulário é o da seção 6 mais grupo: agrupar é o que um manifesto declara e nunca um
    passo, e não existe passo que roda cena.
    """
    cliente, auth, _catalogo = hub
    resposta = await _salvar(cliente, auth, [_cena(passos=(_passo("uuid-1", acao, None),))])
    assert resposta.status == 400
    assert _problemas(await _json(resposta)) == [
        ("cenas[0].passos[0].acao", "cena_acao_desconhecida")
    ]


@pytest.mark.parametrize(
    ("acao", "valor"),
    [
        ("ligar", True),
        ("desligar", 0),
        ("tocar", "x"),
        ("volume", 101),
        ("volume", -1),
        ("volume", "30"),
        ("volume", True),
        ("mudo", 1),
        ("tecla", "power"),
        ("tecla", 1),
        ("temperatura", 15),
        ("temperatura", 31),
        ("temperatura", 22.5),
        ("vento", "turbo"),
        ("fonte", ""),
        ("fonte", "x" * 65),
        ("modo", "a\x00b"),
        ("comando_extra", None),
        ("grupo", 1),
    ],
)
async def test_um_valor_que_a_acao_nao_recebe_e_recusado(hub, acao, valor):
    """The value is judged against what the action of section 6 takes, and nothing wider:
    the JSON true is an int for Python and is not a level, and a key is a word of TECLAS.

    O valor é julgado contra o que a ação da seção 6 recebe, e nada mais largo: o true do JSON
    é int para o Python e não é nível, e uma tecla é uma palavra de TECLAS.
    """
    cliente, auth, _catalogo = hub
    resposta = await _salvar(cliente, auth, [_cena(passos=(_passo("uuid-1", acao, valor),))])
    assert resposta.status == 400
    assert _problemas(await _json(resposta)) == [
        ("cenas[0].passos[0].valor", "cena_valor_invalido")
    ]


@pytest.mark.parametrize(
    ("acao", "valor"),
    [
        ("ligar", None),
        ("volume", 0),
        ("volume", 100),
        ("mudo", False),
        ("tecla", "ok"),
        ("temperatura", 16),
        ("vento", "alto"),
        ("fonte", "HDMI1"),
        ("modo", "seco"),
        ("comando_extra", "preset:3"),
        ("grupo", ""),
        ("grupo", "uuid-2"),
    ],
)
async def test_um_valor_que_a_acao_recebe_e_aceito_e_lido_de_volta(hub, acao, valor):
    """What the manifest of the equipment declares is judged when the step runs, never when
    it is saved: a key or a mode is refused here only when it is outside section 6 itself.

    O que o manifesto do equipamento declara é julgado quando o passo roda, nunca ao salvar:
    uma tecla ou um modo é recusado aqui só quando está fora da própria seção 6.
    """
    cliente, auth, _catalogo = hub
    resposta = await _salvar(cliente, auth, [_cena(passos=(_passo("uuid-1", acao, valor),))])
    assert resposta.status == 200, await resposta.text()
    assert (await _listar(cliente, auth))[0]["passos"][0]["valor"] == valor


async def test_a_recusa_lista_todo_problema_de_uma_vez(hub):
    """One pass fixes the file, the same way section 7 refuses a driver, with the field of
    each problem so the panel points at it.

    Uma passada conserta o arquivo, do mesmo jeito que a seção 7 recusa um driver, com o campo
    de cada problema para o painel apontar para ele.
    """
    cliente, auth, _catalogo = hub
    resposta = await _salvar(
        cliente,
        auth,
        [
            {
                "nome": "Filme",
                "passos": [_passo("uuid-9", "volume", 30), _passo("uuid-1", "ligar", 1)],
                "quando": "18h",
            }
        ],
    )
    assert resposta.status == 400
    assert _problemas(await _json(resposta)) == [
        ("cenas[0].quando", "cena_chave_desconhecida"),
        ("cenas[0].passos[0].equipamento", "cena_equipamento_desconhecido"),
        ("cenas[0].passos[1].valor", "cena_valor_invalido"),
    ]
    assert await _listar(cliente, auth) == []


@pytest.mark.parametrize(
    ("corpo", "campo", "codigo"),
    [
        ({}, "cenas", "cenas_nao_lista"),
        ({"cenas": {}}, "cenas", "cenas_nao_lista"),
        ({"cenas": [5]}, "cenas[0]", "cena_nao_objeto"),
        ({"cenas": [_cena() for _ in range(MAXIMO + 1)]}, "cenas", "cenas_demais"),
        ({"cenas": [_cena("N" * 40) for _ in range(16)]}, "cenas", "nomes_longos"),
        ({"cenas": [_cena("N" * 41)]}, "cenas[0].nome", "cena_nome_invalido"),
        ({"cenas": [_cena("a\nb")]}, "cenas[0].nome", "cena_nome_invalido"),
        ({"cenas": [{"nome": "F", "passos": {}}]}, "cenas[0].passos", "cena_passos_invalidos"),
        (
            {"cenas": [_cena(passos=[_passo()] * (PASSOS_MAXIMOS + 1))]},
            "cenas[0].passos",
            "cena_passos_demais",
        ),
        ({"cenas": [{"nome": "F", "passos": [5]}]}, "cenas[0].passos[0]", "cena_passo_nao_objeto"),
        (
            {"cenas": [_cena(passos=[{**_passo(), "quando": 1}])]},
            "cenas[0].passos[0].quando",
            "cena_chave_desconhecida",
        ),
        (
            {"cenas": [_cena(passos=[_passo(espera_ms=ESPERA_MAXIMA_MS + 1)])]},
            "cenas[0].passos[0].espera_ms",
            "cena_espera_invalida",
        ),
        (
            {"cenas": [_cena(passos=[_passo(espera_ms=True)])]},
            "cenas[0].passos[0].espera_ms",
            "cena_espera_invalida",
        ),
        (
            {"cenas": [{**_cena(), "intervalo_ms": -1}]},
            "cenas[0].intervalo_ms",
            "cena_intervalo_invalido",
        ),
        (
            {"cenas": [{**_cena(), "intervalo_ms": "1000"}]},
            "cenas[0].intervalo_ms",
            "cena_intervalo_invalido",
        ),
    ],
    ids=[
        "sem_cenas",
        "cenas_nao_lista",
        "cena_nao_objeto",
        "cenas_demais",
        "nomes_longos",
        "nome_longo",
        "nome_com_controle",
        "passos_nao_lista",
        "passos_demais",
        "passo_nao_objeto",
        "chave_de_passo",
        "espera_alta",
        "espera_bool",
        "intervalo_negativo",
        "intervalo_texto",
    ],
)
async def test_o_que_nao_e_uma_cena_e_recusado_com_o_codigo_do_campo(hub, corpo, campo, codigo):
    """A scene is data and never program, so everything outside the format answers a stable
    code on the field that broke it, and the two name data points of section 8 are never cut.

    Uma cena é dado e nunca programa, então tudo fora do formato responde um código estável no
    campo que o quebrou, e os dois data points de nomes da seção 8 nunca são cortados.
    """
    cliente, auth, _catalogo = hub
    resposta = await cliente.post("/api/cenas", json=corpo, headers=auth)
    assert resposta.status == 400
    recusa = await _json(resposta)
    assert recusa["code"] == "cenas_invalidas"
    assert (campo, codigo) in _problemas(recusa)
    assert await _listar(cliente, auth) == []


async def test_trinta_e_duas_cenas_de_sessenta_e_quatro_passos_cabem_no_corpo(hub):
    """The route declares its own ceiling, because a login sized body would truncate an
    honest list into a refusal nobody could fix.

    A rota declara o próprio teto, porque um corpo do tamanho de um login truncaria uma lista
    honesta numa recusa que ninguém consegue consertar.
    """
    cliente, auth, _catalogo = hub
    passos = [{"equipamento": "uuid-1", "acao": "ligar"} for _ in range(PASSOS_MAXIMOS)]
    corpo = {"cenas": [{"nome": f"Cena {n}", "passos": passos} for n in range(1, MAXIMO + 1)]}
    assert CORPO_MAXIMO < len(json.dumps(corpo)) <= CORPO_MAXIMO_CENAS
    resposta = await cliente.post("/api/cenas", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    cenas = await _listar(cliente, auth)
    assert len(cenas) == MAXIMO
    assert all(len(cena["passos"]) == PASSOS_MAXIMOS for cena in cenas)


async def test_um_corpo_maior_que_o_teto_da_rota_e_recusado(hub):
    """Whoever holds a session still does not get to make the daemon read a body of any size.

    Quem tem sessão ainda assim não faz o daemon ler um corpo de qualquer tamanho.
    """
    cliente, auth, _catalogo = hub
    assert (await _salvar(cliente, auth, [_cena("Filme")])).status == 200
    resposta = await cliente.post(
        "/api/cenas",
        json={"cenas": [_cena("Festa")], "enchimento": "a" * CORPO_MAXIMO_CENAS},
        headers=auth,
    )
    assert resposta.status == 400
    assert await _json(resposta) == {"ok": False, "code": "corpo_invalido"}
    assert [cena["nome"] for cena in await _listar(cliente, auth)] == ["Filme"]


async def test_um_corpo_json_fundo_demais_e_corpo_invalido_e_nunca_erro_interno(hub):
    """Section 11: a body this daemon cannot read answers a stable code, never a 500 with a
    traceback in the log.

    Seção 11: um corpo que este daemon não consegue ler responde um código estável, nunca um
    500 com traceback no log.
    """
    cliente, auth, _catalogo = hub
    # Why: the scenes route takes a body far larger than a login, so a body deep enough to
    # exhaust the recursion of the parser fits inside its ceiling and really reaches json.loads.
    # Por que: a rota de cenas aceita um corpo bem maior que um login, então um corpo fundo o
    # bastante para esgotar a recursão do parser cabe no teto dela e chega mesmo ao json.loads.
    fundo = "[" * 12000 + "]" * 12000
    resposta = await cliente.post(
        "/api/cenas", data=fundo, headers={**auth, "Content-Type": "application/json"}
    )
    assert resposta.status != 500, await resposta.text()
    assert (await _json(resposta))["code"] == "corpo_invalido"


async def test_executar_responde_na_hora_e_os_passos_chegam_na_ordem_com_a_espera(hub, agenda):
    """The run answers before the first step lands, the steps reach the drivers in the order
    they were saved, and the wait of a step really separates it from the next one: the clock
    of the scenes is in the hand of the test, so the second step only lands once it is let go.

    A execução responde antes de o primeiro passo cair, os passos chegam aos drivers na ordem
    em que foram salvos, e a espera de um passo separa mesmo ele do seguinte: o relógio das
    cenas está na mão do teste, então o segundo passo só cai quando ele é solto.
    """
    cliente, auth, catalogo = hub
    passos = (
        _passo("uuid-1", "volume", 30, espera_ms=ESPERA_REAL_MS),
        _passo("uuid-2", "volume", 10),
        _passo("uuid-ar", "temperatura", 22),
    )
    assert (await _salvar(cliente, auth, [_cena(passos=passos)])).status == 200
    caixa_1, caixa_2 = _aparelho(catalogo, "uuid-1"), _aparelho(catalogo, "uuid-2")
    resposta = await _executar(cliente, auth)
    assert resposta.status == 200, await resposta.text()
    assert await _json(resposta) == {"ok": True, "code": None}
    await _esperar(lambda: caixa_1.chamadas)
    await agenda.girar()
    assert caixa_2.chamadas == []
    assert await agenda.soltar(ESPERA_REAL_MS / 1000) == 1
    await _esperar(lambda: _aparelho(catalogo, "uuid-ar").chamadas)
    assert caixa_1.chamadas == [("volume", 30)]
    assert caixa_2.chamadas == [("volume", 10)]
    assert _aparelho(catalogo, "uuid-ar").chamadas == [("temperatura", 22)]
    assert _eventos(catalogo) == ["uuid-1:volume", "uuid-2:volume", "uuid-ar:temperatura"]


def _recusar(catalogo: dict) -> None:
    _aparelho(catalogo, "uuid-1").recusa = "erro_aparelho"


def _estourar(catalogo: dict) -> None:
    _aparelho(catalogo, "uuid-1").estoura = True


def _nada(catalogo: dict) -> None:
    del catalogo


@pytest.mark.parametrize(
    ("preparar", "primeiro", "codigo"),
    [
        (_recusar, _passo("uuid-1", "volume", 30), "erro_aparelho"),
        (_estourar, _passo("uuid-1", "volume", 30), "erro_aparelho"),
        (_nada, _passo("uuid-1", "tecla", "ok"), "nao_suportado"),
        (_nada, _passo("uuid-ar", "modo", "seco"), "valor_invalido"),
    ],
    ids=["recusa", "estoura", "fora_do_manifesto", "fora_do_vocabulario_do_manifesto"],
)
async def test_um_passo_que_falha_nao_para_a_cena(hub, caplog, preparar, primeiro, codigo):
    """A projector that is off must not stop the lights of the same scene: a refusal, an
    exception, an action the manifest does not declare and a word outside its vocabulary
    are each one failed step, logged with its stable code, and the next step still runs.

    Um projetor desligado não pode parar as luzes da mesma cena: uma recusa, uma exceção, uma
    ação que o manifesto não declara e uma palavra fora do vocabulário dele são cada uma um
    passo que falhou, registrado com o código estável dele, e o passo seguinte roda.
    """
    cliente, auth, catalogo = hub
    preparar(catalogo)
    cena = _cena(passos=(primeiro, _passo("uuid-2", "volume", 15)))
    assert (await _salvar(cliente, auth, [cena])).status == 200
    with caplog.at_level(logging.WARNING):
        assert (await _executar(cliente, auth)).status == 200
        await _esperar(lambda: _aparelho(catalogo, "uuid-2").chamadas)
    assert _aparelho(catalogo, "uuid-2").chamadas == [("volume", 15)]
    assert codigo in caplog.text


async def test_a_mesma_cena_nao_roda_duas_vezes_ao_mesmo_tempo(hub):
    """Two runs of one scene interleave two sequences over the same equipment, and the
    listing says which scene is in flight.

    Duas execuções de uma cena intercalam duas sequências sobre os mesmos equipamentos, e a
    listagem diz qual cena está em curso.
    """
    cliente, auth, catalogo = hub
    assert (await _salvar(cliente, auth, [_cena("Filme"), _cena("Festa")])).status == 200
    caixa = _aparelho(catalogo, "uuid-1")
    caixa.pausa = asyncio.Event()
    assert (await _executar(cliente, auth)).status == 200
    await _esperar(lambda: caixa.chamadas)
    resposta = await _executar(cliente, auth)
    assert resposta.status == 409
    assert await _json(resposta) == {"ok": False, "code": "cena_em_curso"}
    assert [cena["em_curso"] for cena in await _listar(cliente, auth)] == [True, False]
    # Why: another scene is another sequence, and holding it for the first would make the
    # whole house wait for one projector to warm up.
    # Por que: outra cena é outra sequência, e segurá-la pela primeira faria a casa inteira
    # esperar um projetor aquecer.
    assert (await _executar(cliente, auth, 2)).status == 200
    caixa.pausa.set()
    await _ate_terminar(cliente, auth)
    assert caixa.chamadas == [("volume", 30), ("volume", 30)]


async def test_uma_lista_salva_durante_a_execucao_nao_troca_os_passos_em_curso(hub):
    """Half of one file and half of the next is a scene nobody wrote: the run in flight keeps
    the steps it started with, and the next run takes the new list.

    Metade de um arquivo e metade do seguinte é uma cena que ninguém escreveu: a execução em
    curso mantém os passos com que começou, e a execução seguinte toma a lista nova.
    """
    cliente, auth, catalogo = hub
    caixa_1, caixa_2 = _aparelho(catalogo, "uuid-1"), _aparelho(catalogo, "uuid-2")
    caixa_1.pausa = asyncio.Event()
    antiga = _cena(passos=(_passo("uuid-1", "volume", 30), _passo("uuid-2", "volume", 10)))
    assert (await _salvar(cliente, auth, [antiga])).status == 200
    assert (await _executar(cliente, auth)).status == 200
    await _esperar(lambda: caixa_1.chamadas)
    nova = _cena(passos=(_passo("uuid-2", "volume", 99),))
    assert (await _salvar(cliente, auth, [nova])).status == 200
    caixa_1.pausa.set()
    await _ate_terminar(cliente, auth)
    assert caixa_2.chamadas == [("volume", 10)]
    assert (await _executar(cliente, auth)).status == 200
    await _esperar(lambda: len(caixa_2.chamadas) == 2)
    assert caixa_2.chamadas == [("volume", 10), ("volume", 99)]
    assert caixa_1.chamadas == [("volume", 30)]


@pytest.mark.parametrize("numero", ["1", "2", "33", "0", "abc"])
async def test_executar_uma_vaga_que_ninguem_usou_nao_acha_cena(hub, numero):
    """A slot with no step is a number held open for the automations, not a scene.

    Uma vaga sem passo é um número guardado para as automações, e não uma cena.
    """
    cliente, auth, _catalogo = hub
    assert (await _salvar(cliente, auth, [{"nome": "", "passos": []}])).status == 200
    resposta = await _executar(cliente, auth, numero)
    assert resposta.status == 404
    assert await _json(resposta) == {"ok": False, "code": "cena_nao_encontrada"}


@pytest.mark.parametrize("numero", ["abc", "0", "33", "²", "١", "9" * 30, "1.0", "-1"])
async def test_um_numero_de_cena_fora_do_contrato_nunca_e_erro_interno(hub, numero):
    """Section 11: the API answers a stable code, never a 500 with a traceback in the log.

    Why: str.isdigit() is true for the superscript two and int() refuses it, so this path
    answered erro_interno, and a session holder could fill the log with tracebacks at will.

    Seção 11: a API responde um código estável, nunca um 500 com traceback no log.

    Por que: str.isdigit() é verdadeiro para o dois sobrescrito e o int() o recusa, então este
    caminho respondia erro_interno, e quem tem sessão podia encher o log de tracebacks.
    """
    cliente, auth, _catalogo = hub
    assert (await _salvar(cliente, auth, [_cena("Filme")])).status == 200
    resposta = await _executar(cliente, auth, numero)
    assert resposta.status == 404, await resposta.text()
    assert await _json(resposta) == {"ok": False, "code": "cena_nao_encontrada"}


async def test_a_leitura_traz_o_intervalo_padrao_e_o_de_cada_cena(hub):
    """The panel reads the default interval from here, each scene carries its own, and a
    step with no wait of its own answers null, never a zero that would read as an order.

    O painel lê o intervalo padrão daqui, cada cena carrega o dela, e um passo sem espera
    própria responde null, nunca um zero que se leria como ordem.
    """
    cliente, auth, _catalogo = hub
    cena = {**_cena(passos=(_passo(espera_ms=None),)), "intervalo_ms": 250}
    resposta = await _salvar(cliente, auth, [cena])
    assert resposta.status == 200, await resposta.text()
    cenas = await _listar(cliente, auth)
    assert cenas[0]["intervalo_ms"] == 250
    assert cenas[0]["passos"][0]["espera_ms"] is None


async def test_a_config_editada_na_mao_com_uma_cena_invalida_nao_sobe(fabrica_cliente, amb):
    """The route is one door into this field and the file is the other: a value the action
    does not take is refused at boot the same way it is refused at save.

    A rota é uma porta para este campo e o arquivo é a outra: um valor que a ação não recebe é
    recusado no boot do mesmo jeito que é recusado ao salvar.
    """
    _gravar_config(
        amb,
        cenas=[
            {"nome": "Filme", "passos": [{"equipamento": "uuid-1", "acao": "ligar", "valor": 1}]}
        ],
    )
    with pytest.raises(ConfigIncompativel) as erro:
        await fabrica_cliente(catalogo=_catalogo())
    assert "cenas" in str(erro.value)


async def test_uma_cena_da_config_sobre_quem_sumiu_sobe_e_o_passo_e_julgado_ao_rodar(
    fabrica_cliente, posse, bearer, agenda, amb, caplog
):
    """A registration erased by hand must not keep the whole file from loading: the step
    over the missing equipment is judged when it runs, and the rest of the scene still runs.

    Um cadastro apagado na mão não pode impedir o arquivo inteiro de carregar: o passo sobre
    o equipamento que sumiu é julgado quando roda, e o resto da cena roda.
    """
    _gravar_config(
        amb,
        equipamentos=[{"identidade": "uuid-1", "tipo": TIPO, "nome": "Sala", "ip": IP_1}],
        cenas=[
            {
                "nome": "Filme",
                "passos": [
                    {"equipamento": "uuid-sumiu", "acao": "volume", "valor": 30, "espera_ms": 0},
                    {"equipamento": "uuid-1", "acao": "volume", "valor": 15, "espera_ms": 0},
                ],
            }
        ],
    )
    catalogo = _catalogo()
    cliente = await fabrica_cliente(catalogo=catalogo, dormir=agenda.dormir, agora=agenda)
    auth = bearer(await posse(cliente))
    cenas = await _listar(cliente, auth)
    assert [passo["equipamento"] for passo in cenas[0]["passos"]] == ["uuid-sumiu", "uuid-1"]
    with caplog.at_level(logging.WARNING):
        assert (await _executar(cliente, auth)).status == 200
        await _esperar(lambda: _aparelho(catalogo, "uuid-1").chamadas)
    assert _aparelho(catalogo, "uuid-1").chamadas == [("volume", 15)]
    assert "numero_offline" in caplog.text


async def test_um_passo_de_grupo_fora_de_uma_licenca_e_recusado_e_a_cena_segue(hub, caplog):
    """The group is of a licence of audio and video, so an equipment that holds no number has
    no group to form; the step is refused with the code of a capability nobody declared.

    O grupo é de uma licença de áudio e vídeo, então um equipamento que não ocupa número não
    tem grupo para formar; o passo é recusado com o código de uma capacidade que ninguém
    declarou.
    """
    cliente, auth, catalogo = hub
    cena = _cena(passos=(_passo("uuid-2", "grupo", "uuid-1"), _passo("uuid-1", "volume", 15)))
    assert (await _salvar(cliente, auth, [cena])).status == 200
    with caplog.at_level(logging.WARNING):
        assert (await _executar(cliente, auth)).status == 200
        await _esperar(lambda: _aparelho(catalogo, "uuid-1").chamadas)
    assert _aparelho(catalogo, "uuid-2").chamadas == []
    assert _aparelho(catalogo, "uuid-1").chamadas == [("volume", 15)]
    assert "nao_suportado" in caplog.text


async def test_um_passo_sobre_um_equipamento_com_numero_chega_ao_driver_pela_licenca(
    licenciado,
):
    """An equipment that holds a number is commanded through its licence, and solo that is
    the same driver a data point would reach.

    Um equipamento que ocupa número é comandado pela licença dele, e solo isso é o mesmo
    driver que um data point alcançaria.
    """
    cliente, auth, catalogo = licenciado
    passos = (_passo("uuid-1", "volume", 30), _passo("uuid-2", "pausar", None))
    assert (await _salvar(cliente, auth, [_cena(passos=passos)])).status == 200
    assert (await _executar(cliente, auth)).status == 200
    await _esperar(lambda: _aparelho(catalogo, "uuid-2").chamadas)
    assert _aparelho(catalogo, "uuid-1").chamadas == [("volume", 30)]
    assert _aparelho(catalogo, "uuid-2").chamadas == [("pausar", None)]
    assert (await _licenca_json(cliente, auth, "av1"))["grupo"] == 0


async def test_uma_cena_forma_o_grupo_pelo_mestre_e_roteia_o_escravo_por_ele(licenciado):
    """Section 14: the grupo step names the master by identity, the slave joins him, and from
    then on the volume and the transport of the slave go through the master; the empty value
    takes the group down from the master, which is the only speaker that may do it.

    Seção 14: o passo grupo nomeia o mestre pela identidade, o escravo entra nele, e daí em
    diante o volume e o transporte do escravo passam pelo mestre; o valor vazio derruba o grupo
    pelo mestre, que é a única caixa que pode fazer isso.
    """
    cliente, auth, catalogo = licenciado
    caixa_1, caixa_2 = _aparelho(catalogo, "uuid-1"), _aparelho(catalogo, "uuid-2")
    juntas = _cena(
        "Juntas",
        (
            _passo("uuid-2", "grupo", "uuid-1"),
            _passo("uuid-2", "volume", 10),
            _passo("uuid-2", "tocar", None),
        ),
    )
    separadas = _cena("Separadas", (_passo("uuid-1", "grupo", ""),))
    assert (await _salvar(cliente, auth, [juntas, separadas])).status == 200
    assert (await _executar(cliente, auth, 1)).status == 200
    await _esperar(lambda: ("tocar", None) in caixa_1.chamadas)
    assert caixa_2.chamadas == [("entrar_no_grupo", IP_1)]
    assert caixa_1.chamadas == [("volume_de_escravo", (IP_2, 10)), ("tocar", None)]
    licenca = await _licenca_json(cliente, auth, "av1")
    assert licenca["grupo"] == 1
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["mestre", "escravo"]
    assert (await _executar(cliente, auth, 2)).status == 200
    await _esperar(lambda: ("desfazer_grupo", None) in caixa_1.chamadas)
    assert ("desfazer_grupo", None) not in caixa_2.chamadas
    licenca = await _licenca_json(cliente, auth, "av1")
    assert licenca["grupo"] == 0
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["", ""]


async def test_uma_cena_salva_nomeia_os_dps_de_nomes_de_toda_licenca(licenciado):
    """The names of the scenes are what the two name data points of each product publish,
    and the scene data point itself is never in a snapshot, because the chip never echoes.

    Os nomes das cenas são o que os dois data points de nomes de cada produto publicam, e o
    próprio data point de cena nunca está num snapshot, porque o chip nunca ecoa.
    """
    cliente, auth, _catalogo = licenciado
    assert await _licenca(cliente, auth, "ar") == "ar1"
    assert (await _salvar(cliente, auth, [_cena("Filme"), _cena("Festa")])).status == 200
    dps = await _dps(cliente, auth, "av1")
    assert json.loads(dps[str(NOMES_AV_1)]) == {"c": ["Filme", "Festa"]}
    assert json.loads(dps[str(NOMES_AV_2)]) == {"c": []}
    assert str(CENA_AV) not in dps
    dps = await _dps(cliente, auth, "ar1")
    assert json.loads(dps[str(NOMES_AR_1)]) == {"c": ["Filme", "Festa"]}
    assert json.loads(dps[str(NOMES_AR_2)]) == {"c": []}
    assert str(CENA_AR) not in dps


async def test_o_dp_de_cena_de_uma_licenca_de_av_dispara_a_cena(hub):
    """The automation of the platform writes the number on the scene data point of a licence,
    and that is the same run the route starts, with the same refusals.

    A automação da plataforma escreve o número no data point de cena de uma licença, e isso é
    a mesma execução que a rota começa, com as mesmas recusas.
    """
    cliente, auth, catalogo = hub
    assert await _licenca(cliente, auth, "av") == "av1"
    cenas = [
        _cena("Filme", (_passo("uuid-1", "volume", 30),)),
        _cena("Festa", (_passo("uuid-2", "volume", 10),)),
    ]
    assert (await _salvar(cliente, auth, cenas)).status == 200
    caixa_2 = _aparelho(catalogo, "uuid-2")
    caixa_2.pausa = asyncio.Event()
    resposta = await _dp(cliente, auth, "av1", CENA_AV, 2)
    assert resposta.status == 200, await resposta.text()
    await _esperar(lambda: caixa_2.chamadas)
    assert caixa_2.chamadas == [("volume", 10)]
    assert _aparelho(catalogo, "uuid-1").chamadas == []
    resposta = await _dp(cliente, auth, "av1", CENA_AV, 2)
    assert resposta.status == 409
    assert await _json(resposta) == {"ok": False, "code": "cena_em_curso"}
    caixa_2.pausa.set()
    await _ate_terminar(cliente, auth, 2)


@pytest.mark.parametrize(
    ("valor", "status", "codigo"),
    [
        (3, 404, "cena_nao_encontrada"),
        (0, 400, "valor_invalido"),
        (33, 400, "valor_invalido"),
        (True, 400, "valor_invalido"),
        ("cena1", 400, "valor_invalido"),
    ],
)
async def test_o_dp_de_cena_responde_os_codigos_da_rota_de_cenas(hub, valor, status, codigo):
    """Section 11: the scene data point answers the codes of the scene executor, never a 500
    with erro_interno for a scene that simply does not exist, and only 1..32 is a number.

    Seção 11: o data point de cena responde os códigos do executor de cenas, nunca um 500 com
    erro_interno para uma cena que simplesmente não existe, e só 1..32 é número.
    """
    cliente, auth, catalogo = hub
    assert await _licenca(cliente, auth, "av") == "av1"
    assert (await _salvar(cliente, auth, [_cena("Filme"), _cena("Festa")])).status == 200
    resposta = await _dp(cliente, auth, "av1", CENA_AV, valor)
    assert resposta.status == status, await resposta.text()
    assert await _json(resposta) == {"ok": False, "code": codigo}
    assert _aparelho(catalogo, "uuid-1").chamadas == []


async def test_o_dp_de_cena_de_uma_licenca_de_ar_dispara_a_mesma_cena(licenciado):
    """The same number is the same scene on every licence: the licence of air fires it with
    its own scene data point, the air conditioner in its number is reached through that
    licence, and the licence of audio and video fires the very same steps.

    O mesmo número é a mesma cena em toda licença: a licença de ar a dispara com o próprio
    data point de cena, o ar condicionado no número dela é alcançado por aquela licença, e a
    licença de áudio e vídeo dispara os mesmíssimos passos.
    """
    cliente, auth, catalogo = licenciado
    assert await _licenca(cliente, auth, "ar") == "ar1"
    resposta = await cliente.post(
        "/api/licencas/ar1/numeros", json={"numeros": ["uuid-ar"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    passos = (_passo("uuid-1", "volume", 30), _passo("uuid-ar", "temperatura", 22))
    assert (await _salvar(cliente, auth, [_cena("Noite", passos)])).status == 200
    ar = _aparelho(catalogo, "uuid-ar")
    resposta = await _dp(cliente, auth, "ar1", CENA_AR, 1)
    assert resposta.status == 200, await resposta.text()
    await _esperar(lambda: ar.chamadas)
    assert _eventos(catalogo) == ["uuid-1:volume", "uuid-ar:temperatura"]
    await _ate_terminar(cliente, auth)
    resposta = await _dp(cliente, auth, "av1", CENA_AV, 1)
    assert resposta.status == 200, await resposta.text()
    await _esperar(lambda: len(ar.chamadas) == 2)
    assert _eventos(catalogo) == ["uuid-1:volume", "uuid-ar:temperatura"] * 2
    assert ar.chamadas == [("temperatura", 22), ("temperatura", 22)]
    assert _aparelho(catalogo, "uuid-1").chamadas == [("volume", 30), ("volume", 30)]


# defeito em producao: iphub/dpbus/numeros.py:1239
async def test_a_licenca_do_config_sobe_e_a_cena_roda_por_ela(abrir):
    """What the file holds is what the hub is after a reboot: the licence, its numbers and
    the scene come back from config.json, and the scene runs through the licence as before.

    Why: the book of licences hands limite_s to Numeros by position while Numeros only takes
    it by keyword, so a hub whose config.json carries any licence dies on boot with a
    TypeError, which is every reboot of every installation that paired a device.

    O que o arquivo guarda é o que o hub é depois de um reboot: a licença, os números dela e a
    cena voltam do config.json, e a cena roda pela licença como antes.

    Por que: o livro de licenças entrega limite_s ao Numeros por posição enquanto o Numeros só
    o recebe por nome, então um hub cujo config.json carrega qualquer licença morre no boot
    com TypeError, que é todo reboot de toda instalação que pareou um dispositivo.
    """
    catalogo = _catalogo()
    cena = Cena(
        nome="Juntas",
        passos=(Passo("uuid-2", "grupo", "uuid-1", 0), Passo("uuid-2", "volume", 10, 0)),
    )
    cliente, auth = await abrir(
        catalogo,
        equipamentos=_equipamentos(),
        licencas=(Licenca(id="av1", produto="av"),),
        numeros={"av1": ("uuid-1", "uuid-2")},
        cenas=(cena,),
    )
    licenca = await _licenca_json(cliente, auth, "av1")
    assert [numero["identidade"] for numero in licenca["numeros"][:2]] == ["uuid-1", "uuid-2"]
    assert (await _listar(cliente, auth))[0]["nome"] == "Juntas"
    caixa_1 = _aparelho(catalogo, "uuid-1")
    assert (await _executar(cliente, auth)).status == 200
    await _esperar(lambda: caixa_1.chamadas)
    assert _aparelho(catalogo, "uuid-2").chamadas == [("entrar_no_grupo", IP_1)]
    assert caixa_1.chamadas == [("volume_de_escravo", (IP_2, 10))]
    assert (await _licenca_json(cliente, auth, "av1"))["grupo"] == 1
