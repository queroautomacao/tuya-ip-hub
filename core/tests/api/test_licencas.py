# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the licence routes: the licences, the numbers of each, the data points
and the group.

The numbers of section 8 are written by hand in this file. A test that asked the map for
them would agree with any change the map made to the contract, which is exactly what a
contract test exists to catch, and the panel reads these numbers out of these answers.

O contrato das rotas de licença: as licenças, os números de cada uma, os data points e o
grupo.

Os números da seção 8 estão escritos na mão neste arquivo. Um teste que os pedisse ao mapa
concordaria com qualquer mudança que o mapa fizesse no contrato, que é exatamente o que um
teste de contrato existe para pegar, e o painel lê estes números destas respostas.
"""

import json
from dataclasses import dataclass

import pytest

from iphub.api import licencas as rotas
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config, Item, Licenca
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto

TIPO = "multiroom_falso"
OUTRO_TIPO = "multiroom_de_outra_marca"
TIPO_DE_PROJETOR = "projetor_falso"
TIPO_DE_AR = "ar_falso"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")
CAPACIDADES_DE_AR = ("ligar", "desligar", "temperatura", "modo", "vento")
MODOS_DO_AR = ("auto", "frio")
VENTOS_DO_AR = ("auto", "alto")

AV = "av1"
AR = "ar1"
LICENCA_AV = Licenca(id=AV, produto="av")
LICENCA_AR = Licenca(id=AR, produto="ar")
CHAVE = "chave-secreta-da-tuya"

# The numbers of section 8, written by hand on purpose. Product av first, product ar after.
# Os números da seção 8, escritos na mão de propósito. Produto av primeiro, produto ar depois.
LIGADO_1, LIGADO_2, LIGADO_12 = 101, 102, 112
NIVEL_1, NIVEL_2, NIVEL_12 = 121, 122, 132
CENA_AV, GRUPO, COMANDO, ONLINE_AV, MUDOS = 141, 142, 143, 144, 145
ENTRADAS, MODOS, TITULOS, PERFIS_1, PERFIS_5 = 146, 147, 148, 149, 153
NOMES_CENAS_AV = 154
LIGADO_AR_1, TEMPERATURA_AR_1, MODO_AR_1, VENTO_AR_1 = 101, 102, 103, 104
LIGADO_AR_2, LIGADO_AR_8 = 106, 136
CENA_AR, ONLINE_AR, NOMES_AR, NOMES_CENAS_AR = 171, 172, 173, 174

IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"
IP_3 = "192.0.2.13"

FONTES = ("wifi", "line-in")

ESTADO_DA_CAIXA = {"online": True, "volume": 20, "fonte": "wifi", "fontes": FONTES}
ESTADO_DO_PROJETOR = {"online": True, "ligado": False}
ESTADO_DO_AR = {"online": True, "ligado": True, "temperatura": 22, "modo": "frio", "vento": "auto"}


@dataclass(frozen=True)
class _Grupo:
    escravos: tuple = ()


def _manifesto(
    tipo: str,
    categoria: str,
    capacidades: tuple[str, ...],
    modos: tuple[str, ...] = (),
    ventos: tuple[str, ...] = (),
) -> Manifesto:
    textos = {"descricao": "Aparelho de teste"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Aparelho", "en": "Device"},
        categoria=categoria,
        capacidades=capacidades,
        modos=modos,
        ventos=ventos,
        textos={"pt": dict(textos), "en": dict(textos)},
    )


def _fabrica(
    tipo: str = TIPO,
    *,
    categoria: str = "multiroom",
    capacidades: tuple[str, ...] = CAPACIDADES,
    modos: tuple[str, ...] = (),
    ventos: tuple[str, ...] = (),
    estado: dict[str, object] | None = None,
) -> type[Driver]:
    """A driver that records what reached it, so a test proves what never did; the group
    moves are there for the multiroom kind and ignored for every other.

    Um driver que guarda o que chegou nele, para um teste provar o que nunca chegou; os
    movimentos de grupo existem para o tipo multiroom e são ignorados nos outros.
    """
    inicial = dict(ESTADO_DA_CAIXA if estado is None else estado)

    class Falsa(Driver):
        MANIFESTO = _manifesto(tipo, categoria, capacidades, modos, ventos)
        instancias: list["Falsa"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self.grupo = _Grupo()
            self.espelho: str | None = None
            self.escravo_alheio = False
            self._defina(**inicial)
            type(self).instancias.append(self)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            self.chamadas.append((acao, valor))
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

        async def ler_grupo(self) -> _Grupo:
            return self.grupo

        def marcar_grupo(self, dentro: bool) -> None:
            if not dentro:
                self.espelho = None

        def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
            self.espelho = tocando
            self._defina(tocando=tocando, reproduzindo=reproduzindo)

        def e_escravo(self) -> bool:
            return self.escravo_alheio

        def saiu_do_grupo(self) -> bool:
            return False

    Falsa.instancias = []
    return Falsa


def _projetor() -> type[Driver]:
    return _fabrica(
        TIPO_DE_PROJETOR,
        categoria="projetor",
        capacidades=("ligar", "desligar"),
        estado=ESTADO_DO_PROJETOR,
    )


def _ar() -> type[Driver]:
    return _fabrica(
        TIPO_DE_AR,
        categoria="ar_condicionado",
        capacidades=CAPACIDADES_DE_AR,
        modos=MODOS_DO_AR,
        ventos=VENTOS_DO_AR,
        estado=ESTADO_DO_AR,
    )


def _cadastro(
    identidade: str,
    tipo: str = TIPO,
    ip: str = IP_1,
    nome: str = "Sala",
    listas: dict[str, tuple[Item, ...]] | None = None,
) -> Cadastro:
    return Cadastro(identidade=identidade, tipo=tipo, nome=nome, ip=ip, listas=listas or {})


def _corpo_de(licenca: Licenca) -> dict:
    return {
        "produto": licenca.produto,
        "id": licenca.id,
        "nome": licenca.nome,
        "uuid": licenca.uuid,
        "pid": licenca.pid,
        "chave": licenca.chave,
    }


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer):
    """A hub with the catalog and the registrations the test wants, already owned, with the
    licences and the numbers created through the routes; the licence of audio and video is
    there unless the test says otherwise.

    Um hub com o catálogo e os cadastros que o teste quiser, já com dono, com as licenças e
    os números criados pelas rotas; a licença de áudio e vídeo está lá a menos que o teste
    diga o contrário.
    """

    async def criar(
        catalogo: dict,
        *,
        equipamentos=(),
        licencas=(LICENCA_AV,),
        numeros=None,
        cenas=(),
    ):
        cliente = await fabrica_cliente(
            catalogo=catalogo, config=Config(equipamentos=equipamentos, cenas=cenas)
        )
        auth = bearer(await posse(cliente))
        for licenca in licencas:
            resposta = await cliente.post("/api/licencas", json=_corpo_de(licenca), headers=auth)
            assert resposta.status == 200, await resposta.text()
        for id_licenca, ordem in (numeros or {}).items():
            resposta = await cliente.post(
                f"/api/licencas/{id_licenca}/numeros", json={"numeros": list(ordem)}, headers=auth
            )
            assert resposta.status == 200, await resposta.text()
        return cliente, auth

    return criar


@pytest.fixture
def subir_do_arquivo(fabrica_cliente, posse, bearer):
    """A hub that boots with the licences and the numbers already in config.json, which is
    the path every configured hub takes on its second boot.

    Um hub que sobe com as licenças e os números já no config.json, que é o caminho que todo
    hub configurado toma no segundo boot.
    """

    async def criar(catalogo: dict, *, equipamentos=(), licencas=(LICENCA_AV,), numeros=None):
        cliente = await fabrica_cliente(
            catalogo=catalogo,
            config=Config(equipamentos=equipamentos, licencas=licencas, numeros=numeros or {}),
        )
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def duas(abrir):
    """Two speakers of the same kind in numbers 1 and 2 of the licence of audio and video,
    which is the smallest real group.

    Duas caixas do mesmo tipo nos números 1 e 2 da licença de áudio e vídeo, que é o menor
    grupo real.
    """
    classe = _fabrica()
    cliente, auth = await abrir(
        {TIPO: classe},
        equipamentos=(
            _cadastro("uuid-1", ip=IP_1, nome="Sala"),
            _cadastro("uuid-2", ip=IP_2, nome="Cozinha"),
        ),
        numeros={AV: ("uuid-1", "uuid-2")},
    )
    return cliente, auth, classe


@pytest.fixture
async def ar(abrir):
    """One air conditioner in number 1 of a licence of air.

    Um ar condicionado no número 1 de uma licença de ar.
    """
    classe = _ar()
    cliente, auth = await abrir(
        {TIPO_DE_AR: classe},
        equipamentos=(_cadastro("uuid-ar", tipo=TIPO_DE_AR, nome="Ar da sala"),),
        licencas=(LICENCA_AR,),
        numeros={AR: ("uuid-ar",)},
    )
    return cliente, auth, classe


def _caixa(classe, identidade: str):
    return next(caixa for caixa in classe.instancias if caixa.cadastro.identidade == identidade)


async def _json(resposta) -> dict:
    return await resposta.json()


def _em_disco(amb) -> dict:
    return json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))


async def _licenca(cliente, auth, id_licenca: str = AV) -> dict:
    corpo = await _json(await cliente.get("/api/licencas", headers=auth))
    assert corpo["ok"] is True
    return next(licenca for licenca in corpo["licencas"] if licenca["id"] == id_licenca)


async def _dps(cliente, auth, id_licenca: str = AV) -> dict:
    resposta = await cliente.get(f"/api/licencas/{id_licenca}/dps", headers=auth)
    assert resposta.status == 200, await resposta.text()
    return await _json(resposta)


async def test_um_hub_sem_licenca_lista_os_dois_produtos(abrir):
    """Section 6: zero licences is a normal state, and the panel reads the two products and
    the report policy from here instead of carrying a copy of section 8.

    Seção 6: zero licença é estado normal, e o painel lê os dois produtos e a política de
    reports daqui em vez de carregar uma cópia da seção 8.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    corpo = await _json(await cliente.get("/api/licencas", headers=auth))
    assert corpo["ok"] is True
    assert corpo["licencas"] == []
    assert corpo["produtos"] == {"ar": 8, "av": 12}
    assert corpo["reports_por_dia"] == 300
    assert corpo["aviso_do_dia"] == 250


async def test_uma_licenca_vazia_ainda_lista_todos_os_numeros(abrir):
    """Zero equipment is a normal state, and the POSITION is the contract.

    Zero equipamento é estado normal, e a POSIÇÃO é o contrato.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=(LICENCA_AV, LICENCA_AR))
    av = await _licenca(cliente, auth, AV)
    assert av["produto"] == "av"
    assert av["capacidade"] == 12
    assert [numero["numero"] for numero in av["numeros"]] == list(range(1, 13))
    assert all(numero["identidade"] == "" and numero["estado"] is None for numero in av["numeros"])
    assert all(numero["papel"] == "" for numero in av["numeros"])
    assert av["grupo"] == 0
    assert av["chave_definida"] is False
    assert av["reports_do_dia"] == 0
    assert av["ouvintes"] == 0
    ar = await _licenca(cliente, auth, AR)
    assert ar["capacidade"] == 8
    assert [numero["numero"] for numero in ar["numeros"]] == list(range(1, 9))


async def test_cada_numero_carrega_os_data_points_da_secao_8(abrir):
    """The panel reads the numbering from here, so it never carries a copy of section 8.

    O painel lê a numeração daqui, então ele nunca carrega uma cópia da seção 8.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=(LICENCA_AV, LICENCA_AR))
    av = (await _licenca(cliente, auth, AV))["numeros"]
    assert av[0]["dps"] == {"ligado": LIGADO_1, "nivel": NIVEL_1}
    assert av[1]["dps"] == {"ligado": LIGADO_2, "nivel": NIVEL_2}
    assert av[11]["dps"] == {"ligado": LIGADO_12, "nivel": NIVEL_12}
    ar = (await _licenca(cliente, auth, AR))["numeros"]
    assert ar[0]["dps"] == {
        "ligado": LIGADO_AR_1,
        "temperatura": TEMPERATURA_AR_1,
        "modo": MODO_AR_1,
        "vento": VENTO_AR_1,
    }
    assert ar[1]["dps"]["ligado"] == LIGADO_AR_2
    assert ar[7]["dps"] == {
        "ligado": LIGADO_AR_8,
        "temperatura": LIGADO_AR_8 + 1,
        "modo": LIGADO_AR_8 + 2,
        "vento": LIGADO_AR_8 + 3,
    }


# defeito em producao: core/iphub/dpbus/numeros.py:1239
async def test_uma_licenca_gravada_sobe_do_arquivo_com_os_numeros(subir_do_arquivo):
    """The second boot of every configured hub: the licences and the numbers come back from
    config.json, and a hub that cannot read its own file has no bridge and no panel.

    O segundo boot de todo hub configurado: as licenças e os números voltam do config.json, e
    um hub que não consegue ler o próprio arquivo não tem ponte nem painel.
    """
    cliente, auth = await subir_do_arquivo(
        {TIPO: _fabrica()},
        equipamentos=(_cadastro("uuid-1"), _cadastro("uuid-2", ip=IP_2, nome="Cozinha")),
        licencas=(LICENCA_AV, LICENCA_AR),
        numeros={AV: ("uuid-1", "uuid-2")},
    )
    licenca = await _licenca(cliente, auth)
    assert [numero["identidade"] for numero in licenca["numeros"][:2]] == ["uuid-1", "uuid-2"]
    assert licenca["numeros"][0]["estado"]["online"] is True
    assert (await _licenca(cliente, auth, AR))["capacidade"] == 8


async def test_criar_uma_licenca_gera_o_id_e_chega_ao_arquivo(abrir, amb):
    """A licence that lived only in memory would vanish on the next boot while the device on
    the platform stayed paired to a hub that no longer knows it.

    Uma licença que vivesse só na memória sumiria no próximo boot enquanto o dispositivo na
    plataforma seguiria pareado a um hub que não a conhece mais.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    resposta = await cliente.post("/api/licencas", json={"produto": "av"}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    licenca = (await _json(resposta))["licenca"]
    assert licenca["id"] == "av1"
    assert licenca["produto"] == "av"
    assert licenca["capacidade"] == 12
    assert len(licenca["numeros"]) == 12
    assert (licenca["nome"], licenca["uuid"], licenca["pid"]) == ("", "", "")
    assert licenca["chave_definida"] is False
    segunda = await cliente.post("/api/licencas", json={"produto": "av"}, headers=auth)
    assert (await _json(segunda))["licenca"]["id"] == "av2"
    de_ar = await cliente.post("/api/licencas", json={"produto": "ar"}, headers=auth)
    assert (await _json(de_ar))["licenca"]["id"] == "ar1"
    assert (await _json(de_ar))["licenca"]["capacidade"] == 8
    assert _em_disco(amb)["licencas"] == [
        {"id": "av1", "produto": "av", "nome": "", "uuid": "", "pid": "", "chave": ""},
        {"id": "av2", "produto": "av", "nome": "", "uuid": "", "pid": "", "chave": ""},
        {"id": "ar1", "produto": "ar", "nome": "", "uuid": "", "pid": "", "chave": ""},
    ]
    corpo = await _json(await cliente.get("/api/licencas", headers=auth))
    assert [licenca["id"] for licenca in corpo["licencas"]] == ["av1", "av2", "ar1"]


async def test_o_id_nomeado_pelo_corpo_vale_e_o_repetido_e_recusado(abrir, amb):
    """The id is a key of config.json and a path segment, so two licences never share one,
    whatever their product.

    O id é chave do config.json e segmento de caminho, então duas licenças nunca dividem um,
    seja qual for o produto delas.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    resposta = await cliente.post(
        "/api/licencas", json={"produto": "av", "id": "sala"}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    assert (await _json(resposta))["licenca"]["id"] == "sala"
    for produto in ("av", "ar"):
        resposta = await cliente.post(
            "/api/licencas", json={"produto": produto, "id": "sala"}, headers=auth
        )
        assert resposta.status == 409
        assert (await _json(resposta))["code"] == "licenca_repetida"
    assert [licenca["id"] for licenca in _em_disco(amb)["licencas"]] == ["sala"]


@pytest.mark.parametrize(
    "bruto", ["Sala", "-sala", "a" * 41, "sala/1", "sala 1", "sala\n", "sala.1", 1, ["sala"]]
)
async def test_um_id_fora_do_alfabeto_e_licenca_invalida(abrir, amb, bruto):
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    resposta = await cliente.post(
        "/api/licencas", json={"produto": "av", "id": bruto}, headers=auth
    )
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "licenca_invalida"
    assert _em_disco(amb)["licencas"] == []


@pytest.mark.parametrize("corpo", [{"produto": "tv"}, {}, {"produto": 1}, {"produto": "AV"}])
async def test_um_produto_que_a_secao_8_nao_tem_e_recusado(abrir, amb, corpo):
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    resposta = await cliente.post("/api/licencas", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "produto_invalido"
    assert _em_disco(amb)["licencas"] == []


async def test_a_chave_e_gravada_e_nunca_devolvida(abrir, amb):
    """Section 9: the chave is the credential of the device on the platform, so the answer
    carries only whether one is defined.

    Seção 9: a chave é a credencial do dispositivo na plataforma, então a resposta leva só se
    há uma definida.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    corpo = {
        "produto": "av",
        "nome": "Casa",
        "uuid": "uuid-da-tuya",
        "pid": "pid123",
        "chave": CHAVE,
    }
    resposta = await cliente.post("/api/licencas", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    licenca = (await _json(resposta))["licenca"]
    assert (licenca["nome"], licenca["uuid"], licenca["pid"]) == ("Casa", "uuid-da-tuya", "pid123")
    assert licenca["chave_definida"] is True
    assert "chave" not in licenca
    assert _em_disco(amb)["licencas"][0]["chave"] == CHAVE
    listada = await _licenca(cliente, auth)
    assert listada["chave_definida"] is True
    assert "chave" not in listada


async def test_editar_sem_um_campo_mantem_o_guardado_e_vazio_apaga(abrir, amb):
    """An edit that only fixes the name must not erase the credential of the device; an empty
    string erases it on purpose.

    Uma edição que só conserta o nome não pode apagar a credencial do dispositivo; a string
    vazia apaga de propósito.
    """
    guardada = Licenca(
        id=AV, produto="av", nome="Casa", uuid="uuid-da-tuya", pid="pid123", chave=CHAVE
    )
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=(guardada,))
    resposta = await cliente.post(f"/api/licencas/{AV}", json={"nome": "Sítio"}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    licenca = (await _json(resposta))["licenca"]
    assert (licenca["nome"], licenca["uuid"], licenca["pid"]) == ("Sítio", "uuid-da-tuya", "pid123")
    assert licenca["chave_definida"] is True
    assert _em_disco(amb)["licencas"][0]["chave"] == CHAVE
    resposta = await cliente.post(
        f"/api/licencas/{AV}", json={"chave": "", "uuid": ""}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    licenca = (await _json(resposta))["licenca"]
    assert licenca["chave_definida"] is False
    assert licenca["uuid"] == ""
    assert licenca["pid"] == "pid123"
    em_disco = _em_disco(amb)["licencas"][0]
    assert (em_disco["chave"], em_disco["uuid"], em_disco["pid"]) == ("", "", "pid123")


async def test_o_produto_de_uma_licenca_nunca_muda(duas, amb):
    """The product decides the table of section 8 and the numbers already assigned; a
    licence of the other product is another licence.

    O produto decide a tabela da seção 8 e os números já atribuídos; uma licença do outro
    produto é outra licença.
    """
    cliente, auth, _classe = duas
    resposta = await cliente.post(f"/api/licencas/{AV}", json={"produto": "ar"}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "produto_invalido"
    resposta = await cliente.post(
        f"/api/licencas/{AV}", json={"produto": "av", "nome": "Casa"}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    licenca = await _licenca(cliente, auth)
    assert licenca["nome"] == "Casa"
    assert [numero["identidade"] for numero in licenca["numeros"][:2]] == ["uuid-1", "uuid-2"]
    assert _em_disco(amb)["numeros"] == {AV: ["uuid-1", "uuid-2"]}


@pytest.mark.parametrize(
    "corpo",
    [
        {"nome": "N" * 41},
        {"nome": 5},
        {"uuid": "u" * 65},
        {"uuid": "identidade-com-ç"},
        {"pid": "pid\n"},
        {"chave": "c" * 129},
        {"chave": ["x"]},
        {"chave": None},
    ],
)
async def test_um_campo_fora_do_teto_ou_fora_do_ascii_e_licenca_invalida(abrir, amb, corpo):
    cliente, auth = await abrir({TIPO: _fabrica()})
    resposta = await cliente.post(f"/api/licencas/{AV}", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "licenca_invalida"
    assert _em_disco(amb)["licencas"] == [
        {"id": AV, "produto": "av", "nome": "", "uuid": "", "pid": "", "chave": ""}
    ]


async def test_o_nome_aceita_o_alfabeto_do_cliente_e_e_aparado(abrir):
    """The name is read by a person and the identifiers by the platform, so only the second
    kind is held to ASCII.

    O nome é lido por uma pessoa e os identificadores pela plataforma, então só o segundo tipo
    é preso ao ASCII.
    """
    cliente, auth = await abrir({TIPO: _fabrica()})
    resposta = await cliente.post(f"/api/licencas/{AV}", json={"nome": "  Sessão  "}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert (await _json(resposta))["licenca"]["nome"] == "Sessão"


@pytest.mark.parametrize(
    ("metodo", "caminho", "corpo"),
    [
        ("POST", "/api/licencas/zzz", {"nome": "x"}),
        ("DELETE", "/api/licencas/zzz", None),
        ("POST", "/api/licencas/zzz/numeros", {"numeros": []}),
        ("GET", "/api/licencas/zzz/dps", None),
        ("POST", "/api/licencas/zzz/grupo", {"v": 0}),
        ("GET", "/api/licencas/zzz/qr", None),
    ],
)
async def test_uma_licenca_que_nao_existe_e_404(duas, metodo, caminho, corpo):
    cliente, auth, _classe = duas
    resposta = await cliente.request(metodo, caminho, json=corpo, headers=auth)
    assert resposta.status == 404, await resposta.text()
    assert (await _json(resposta))["code"] == "licenca_nao_encontrada"


async def test_apagar_a_licenca_leva_os_numeros_e_deixa_o_equipamento(duas, amb):
    """Section 9: removing a licence empties its numbers without removing equipment.

    Seção 9: apagar uma licença esvazia os números dela sem apagar equipamento.
    """
    cliente, auth, _classe = duas
    resposta = await cliente.delete(f"/api/licencas/{AV}", headers=auth)
    assert resposta.status == 200, await resposta.text()
    corpo = await _json(await cliente.get("/api/licencas", headers=auth))
    assert corpo["licencas"] == []
    em_disco = _em_disco(amb)
    assert em_disco["licencas"] == []
    assert em_disco["numeros"] == {}
    equipamentos = (await _json(await cliente.get("/api/equipamentos", headers=auth)))[
        "equipamentos"
    ]
    assert [eq["identidade"] for eq in equipamentos] == ["uuid-1", "uuid-2"]
    assert all(eq["licenca"] is None and eq["numero"] is None for eq in equipamentos)
    resposta = await cliente.delete(f"/api/licencas/{AV}", headers=auth)
    assert resposta.status == 404
    assert (await _json(resposta))["code"] == "licenca_nao_encontrada"


async def test_o_equipamento_responde_a_licenca_e_o_numero_que_ocupa(abrir):
    """The panel shows the number on the app in the detail of each equipment, and reads it
    from the registration instead of walking every licence.

    O painel mostra o número no app no detalhe de cada equipamento, e o lê do cadastro em vez
    de percorrer toda licença.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica()},
        equipamentos=(
            _cadastro("uuid-1", ip=IP_1),
            _cadastro("uuid-2", ip=IP_2, nome="Cozinha"),
            _cadastro("uuid-3", ip=IP_3, nome="Quarto"),
        ),
        numeros={AV: ("uuid-2", "", "uuid-1")},
    )
    equipamentos = (await _json(await cliente.get("/api/equipamentos", headers=auth)))[
        "equipamentos"
    ]
    por_identidade = {eq["identidade"]: eq for eq in equipamentos}
    assert (por_identidade["uuid-2"]["licenca"], por_identidade["uuid-2"]["numero"]) == (AV, 1)
    assert (por_identidade["uuid-1"]["licenca"], por_identidade["uuid-1"]["numero"]) == (AV, 3)
    assert (por_identidade["uuid-3"]["licenca"], por_identidade["uuid-3"]["numero"]) == (None, None)
    assert por_identidade["uuid-1"]["listas"] == {}


async def test_o_qr_leva_o_uuid_e_o_pid_e_nunca_a_chave(abrir):
    """Section 9: the QR code carries what the app needs to pair and never the credential.

    Seção 9: o QR code leva o que o app precisa para parear e nunca a credencial.
    """
    completa = Licenca(id=AV, produto="av", uuid="uuid-da-tuya", pid="pid123", chave=CHAVE)
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=(completa,))
    resposta = await cliente.get(f"/api/licencas/{AV}/qr", headers=auth)
    assert resposta.status == 200, await resposta.text()
    texto = await resposta.text()
    assert CHAVE not in texto
    corpo = json.loads(texto)
    assert corpo["conteudo"] == "https://smartapp.tuya.com/s/p?p=pid123&uuid=uuid-da-tuya&v=2.0"
    assert (corpo["uuid"], corpo["pid"]) == ("uuid-da-tuya", "pid123")


@pytest.mark.parametrize("corpo", [{"uuid": ""}, {"pid": ""}])
async def test_o_qr_de_uma_licenca_sem_identidade_e_licenca_incompleta(abrir, corpo):
    completa = Licenca(id=AV, produto="av", uuid="uuid-da-tuya", pid="pid123")
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=(completa,))
    assert (await cliente.post(f"/api/licencas/{AV}", json=corpo, headers=auth)).status == 200
    resposta = await cliente.get(f"/api/licencas/{AV}/qr", headers=auth)
    assert resposta.status == 409
    assert (await _json(resposta))["code"] == "licenca_incompleta"


async def test_a_ordem_salva_chega_ao_arquivo_e_a_leitura(abrir, amb):
    """A number that lives only in memory would move a speaker back on the next boot.

    Um número que vivesse só na memória moveria uma caixa de volta no próximo boot.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica()},
        equipamentos=(_cadastro("uuid-1"), _cadastro("uuid-2", ip=IP_2, nome="Cozinha")),
    )
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": ["uuid-2", "", "uuid-1"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    assert (await _json(resposta))["numeros"] == ["uuid-2", "", "uuid-1"]
    assert _em_disco(amb)["numeros"] == {AV: ["uuid-2", "", "uuid-1"]}
    numeros = (await _licenca(cliente, auth))["numeros"]
    assert [numero["identidade"] for numero in numeros] == ["uuid-2", "", "uuid-1"] + [""] * 9
    assert numeros[0]["nome"] == "Cozinha"
    assert numeros[0]["tipo"] == TIPO
    assert numeros[0]["estado"]["online"] is True
    assert numeros[0]["estado"]["fontes"] == list(FONTES)
    assert numeros[1]["estado"] is None


@pytest.mark.parametrize(("licenca", "cabem"), [(LICENCA_AV, 12), (LICENCA_AR, 8)])
async def test_uma_ordem_maior_que_o_contrato_e_recusada(abrir, licenca, cabem):
    """Section 8 numbers twelve equipment of audio and video and eight machines of air, and
    there is no next one.

    A seção 8 numera doze equipamentos de áudio e vídeo e oito máquinas de ar, e não existe
    um seguinte.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=(licenca,))
    caminho = f"/api/licencas/{licenca.id}/numeros"
    resposta = await cliente.post(caminho, json={"numeros": [""] * (cabem + 1)}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "numeros_demais"
    resposta = await cliente.post(caminho, json={"numeros": [""] * cabem}, headers=auth)
    assert resposta.status == 200, await resposta.text()


async def test_uma_identidade_que_ninguem_cadastrou_nao_ocupa_um_numero(abrir, amb):
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),))
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": ["uuid-9"]}, headers=auth
    )
    assert resposta.status == 404
    assert (await _json(resposta))["code"] == "eq_nao_encontrado"
    assert _em_disco(amb)["numeros"] == {}


async def test_a_mesma_caixa_nao_ocupa_dois_numeros(abrir):
    """One speaker in two numbers answers the level of two numbers on the bus.

    Uma caixa em dois números responde o nível de dois números no barramento.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),))
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": ["uuid-1", "uuid-1"]}, headers=auth
    )
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "numero_repetido"


async def test_a_mesma_caixa_nao_ocupa_numero_em_duas_licencas(abrir, amb):
    """One equipment in two licences would be two devices of the platform contradicting each
    other about the same speaker; the number moves only after the first licence lets go.

    Um equipamento em duas licenças seriam dois dispositivos da plataforma se contradizendo
    sobre a mesma caixa; o número só muda depois que a primeira licença o solta.
    """
    outra = Licenca(id="av2", produto="av")
    cliente, auth = await abrir(
        {TIPO: _fabrica()},
        equipamentos=(_cadastro("uuid-1"),),
        licencas=(LICENCA_AV, outra),
        numeros={AV: ("uuid-1",)},
    )
    resposta = await cliente.post(
        "/api/licencas/av2/numeros", json={"numeros": ["uuid-1"]}, headers=auth
    )
    assert resposta.status == 409
    assert (await _json(resposta))["code"] == "numero_ocupado"
    # Why: the file carries the order of every licence of the book, an empty one included.
    # Por que: o arquivo leva a ordem de toda licença do livro, inclusive uma vazia.
    assert _em_disco(amb)["numeros"] == {AV: ["uuid-1"], "av2": []}
    assert (
        await cliente.post(f"/api/licencas/{AV}/numeros", json={"numeros": []}, headers=auth)
    ).status == 200
    resposta = await cliente.post(
        "/api/licencas/av2/numeros", json={"numeros": ["uuid-1"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    assert _em_disco(amb)["numeros"] == {AV: [], "av2": ["uuid-1"]}
    equipamentos = (await _json(await cliente.get("/api/equipamentos", headers=auth)))[
        "equipamentos"
    ]
    assert (equipamentos[0]["licenca"], equipamentos[0]["numero"]) == ("av2", 1)


async def test_um_ar_condicionado_so_entra_numa_licenca_de_ar_e_uma_caixa_so_numa_de_av(abrir, amb):
    """Section 8: the product of a licence is decided by the category of the manifest, in
    both directions.

    Seção 8: o produto de uma licença é decidido pela categoria do manifesto, nos dois
    sentidos.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica(), TIPO_DE_AR: _ar()},
        equipamentos=(
            _cadastro("uuid-1"),
            _cadastro("uuid-ar", tipo=TIPO_DE_AR, ip=IP_2, nome="Ar da sala"),
        ),
        licencas=(LICENCA_AV, LICENCA_AR),
    )
    for id_licenca, identidade in ((AV, "uuid-ar"), (AR, "uuid-1")):
        resposta = await cliente.post(
            f"/api/licencas/{id_licenca}/numeros", json={"numeros": [identidade]}, headers=auth
        )
        assert resposta.status == 400, await resposta.text()
        assert (await _json(resposta))["code"] == "produto_incompativel"
    assert _em_disco(amb)["numeros"] == {}
    for id_licenca, identidade in ((AV, "uuid-1"), (AR, "uuid-ar")):
        resposta = await cliente.post(
            f"/api/licencas/{id_licenca}/numeros", json={"numeros": [identidade]}, headers=auth
        )
        assert resposta.status == 200, await resposta.text()
    ar = (await _licenca(cliente, auth, AR))["numeros"][0]
    assert ar["identidade"] == "uuid-ar"
    assert ar["estado"]["temperatura"] == 22
    assert ar["papel"] == ""


@pytest.mark.parametrize(
    "corpo",
    [
        {"numeros": "uuid-1"},
        {"numeros": [1]},
        {"numeros": [["uuid-1"]]},
        {"numeros": {"1": "uuid-1"}},
        {},
    ],
)
async def test_o_que_nao_e_lista_de_identidade_e_identidade_invalida(abrir, amb, corpo):
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),))
    resposta = await cliente.post(f"/api/licencas/{AV}/numeros", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "identidade_invalida"
    assert _em_disco(amb)["numeros"] == {}


async def test_perfis_que_nao_cabem_nas_cinco_strings_sao_recusados(abrir, amb):
    """Section 8: the profiles of a licence share five strings of 255 bytes, and an order that
    does not pack is refused with the integrator at the keyboard instead of taking every
    profile off the bus.

    Seção 8: os perfis de uma licença dividem cinco strings de 255 bytes, e uma ordem que não
    cabe é recusada com o integrador no teclado em vez de tirar todo perfil do barramento.
    """
    # Why: ten inputs of sixteen characters make a profile of about 190 bytes, which fits the
    # 200 of one registration and leaves no string with room for a second one.
    # Por que: dez entradas de dezesseis caracteres fazem um perfil de uns 190 bytes, que cabe
    # nos 200 de um cadastro e não deixa string nenhuma com espaço para um segundo.
    entradas = tuple(Item(rotulo=f"Entrada {n:02d} longa", valor=f"in{n}") for n in range(10))
    equipamentos = tuple(
        _cadastro(
            f"uuid-{n}", ip=f"192.0.2.{10 + n}", nome=f"Caixa {n}", listas={"entradas": entradas}
        )
        for n in range(1, 7)
    )
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=equipamentos)
    identidades = [cadastro.identidade for cadastro in equipamentos]
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": identidades}, headers=auth
    )
    assert resposta.status == 400, await resposta.text()
    assert (await _json(resposta))["code"] == "perfis_longos"
    assert _em_disco(amb)["numeros"] == {}
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": identidades[:5]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    dps = (await _dps(cliente, auth))["dps"]
    assert all(
        dps[str(PERFIS_1 + indice)].startswith(f"{indice + 1}|au|Caixa") for indice in range(5)
    )


# defeito em producao: core/iphub/dpbus/numeros.py:1239
async def test_uma_ordem_editada_na_mao_que_o_livro_recusa_sobe_com_o_numero_vazio(
    subir_do_arquivo,
):
    """The route validates an order and config.json does not, so a number the book refuses
    on boot is left empty instead of moving the rest.

    A rota valida uma ordem e o config.json não, então um número que o livro recusa no boot
    fica vazio em vez de mover o resto.
    """
    cliente, auth = await subir_do_arquivo(
        {TIPO: _fabrica(), TIPO_DE_AR: _ar()},
        equipamentos=(
            _cadastro("uuid-2", ip=IP_2, nome="Cozinha"),
            _cadastro("uuid-ar", tipo=TIPO_DE_AR, ip=IP_3, nome="Ar"),
        ),
        numeros={AV: ("uuid-9", "uuid-2", "uuid-ar")},
    )
    numeros = (await _licenca(cliente, auth))["numeros"]
    assert [numero["identidade"] for numero in numeros[:3]] == ["", "uuid-2", ""]


async def test_o_snapshot_traz_o_reportavel_e_nunca_o_que_e_so_envio(duas):
    """Section 8: the chip never echoes, so a scene and a command are never reported, and an
    always-on equipment stays silent on its power data point.

    Seção 8: o chip nunca ecoa, então uma cena e um comando nunca são reportados, e um
    equipamento always-on fica calado no data point de ligar.
    """
    cliente, auth, _classe = duas
    corpo = await _dps(cliente, auth)
    assert corpo["produto"] == "av"
    assert corpo["reports_do_dia"] == 0
    dps = corpo["dps"]
    assert dps[str(ONLINE_AV)] == 0b11
    assert dps[str(NIVEL_1)] == 20
    assert dps[str(NIVEL_2)] == 20
    assert dps[str(GRUPO)] == 0
    assert dps[str(MUDOS)] == 0
    assert dps[str(PERFIS_1)] == "1|au|Sala||||NMPG;2|au|Cozinha||||NMPG"
    assert dps[str(PERFIS_5)] == ""
    assert json.loads(dps[str(NOMES_CENAS_AV)]) == {"c": []}
    assert str(CENA_AV) not in dps
    assert str(COMANDO) not in dps
    assert str(LIGADO_1) not in dps


async def test_o_snapshot_descreve_a_tabela_da_secao_8(duas):
    """The scene editor of the panel reads the table from here, and never guesses it.

    O editor de cenas do painel lê a tabela daqui, e nunca a adivinha.
    """
    cliente, auth, _classe = duas
    tabela = {item["dpid"]: item for item in (await _dps(cliente, auth))["mapa"]}
    assert len(tabela) == 39
    assert tabela[NIVEL_1] == {
        "dpid": NIVEL_1,
        "numero": 1,
        "indice": 0,
        "funcao": "nivel",
        "tipo": "value",
        "sentido": "rw",
        "classe": "a",
        "valores": [],
        "minimo": 0,
        "maximo": 100,
        "empurrado": True,
    }
    assert (tabela[LIGADO_1]["tipo"], tabela[LIGADO_1]["numero"]) == ("bool", 1)
    assert tabela[LIGADO_12]["numero"] == 12
    assert tabela[ONLINE_AV]["sentido"] == "reporte"
    assert tabela[CENA_AV]["sentido"] == "envio"
    assert (tabela[CENA_AV]["minimo"], tabela[CENA_AV]["maximo"]) == (1, 32)
    assert (tabela[GRUPO]["sentido"], tabela[GRUPO]["maximo"]) == ("rw", 12)
    assert (tabela[COMANDO]["tipo"], tabela[COMANDO]["sentido"]) == ("string", "envio")
    assert tabela[MUDOS]["classe"] == "b"
    assert tabela[ENTRADAS]["classe"] == "b"
    assert tabela[TITULOS]["empurrado"] is False
    assert (tabela[PERFIS_1]["indice"], tabela[PERFIS_5]["indice"]) == (1, 5)
    assert tabela[NOMES_CENAS_AV]["tipo"] == "string"


async def test_a_tabela_de_uma_licenca_de_ar_e_a_da_secao_8(ar):
    cliente, auth, _classe = ar
    corpo = await _dps(cliente, auth, AR)
    assert corpo["produto"] == "ar"
    tabela = {item["dpid"]: item for item in corpo["mapa"]}
    assert len(tabela) == 37
    assert (tabela[LIGADO_AR_1]["tipo"], tabela[LIGADO_AR_1]["numero"]) == ("bool", 1)
    assert (tabela[TEMPERATURA_AR_1]["tipo"], tabela[TEMPERATURA_AR_1]["funcao"]) == (
        "value",
        "temperatura",
    )
    assert (tabela[TEMPERATURA_AR_1]["minimo"], tabela[TEMPERATURA_AR_1]["maximo"]) == (16, 30)
    assert tabela[MODO_AR_1]["valores"] == ["auto", "frio", "quente", "vento", "seco"]
    assert tabela[VENTO_AR_1]["valores"] == ["auto", "baixo", "medio", "alto"]
    assert tabela[LIGADO_AR_2]["numero"] == 2
    assert tabela[LIGADO_AR_8]["numero"] == 8
    assert tabela[CENA_AR]["sentido"] == "envio"
    assert tabela[ONLINE_AR]["sentido"] == "reporte"
    assert tabela[NOMES_AR]["tipo"] == "string"
    assert GRUPO not in tabela


async def test_o_snapshot_de_uma_licenca_de_ar_le_o_estado_tipado(ar):
    cliente, auth, _classe = ar
    dps = (await _dps(cliente, auth, AR))["dps"]
    assert dps[str(LIGADO_AR_1)] is True
    assert dps[str(TEMPERATURA_AR_1)] == 22
    assert dps[str(MODO_AR_1)] == "frio"
    assert dps[str(VENTO_AR_1)] == "auto"
    assert dps[str(ONLINE_AR)] == 0b1
    assert json.loads(dps[str(NOMES_AR)]) == {"m": ["Ar da sala"]}
    assert json.loads(dps[str(NOMES_CENAS_AR)]) == {"c": []}
    assert str(CENA_AR) not in dps
    assert str(LIGADO_AR_2) not in dps


async def test_um_set_de_data_point_chega_a_caixa_do_numero(duas):
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/licencas/{AV}/dp/{NIVEL_1}", json={"v": 40}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert _caixa(classe, "uuid-1").chamadas == [("volume", 40)]
    assert _caixa(classe, "uuid-2").chamadas == []


async def test_um_set_de_data_point_de_report_nao_chega_a_caixa(duas):
    """Section 8: a report is only ever born of real state, so 144 takes no set.

    Seção 8: um report só nasce de estado real, então o 144 não aceita set.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(
        f"/api/licencas/{AV}/dp/{ONLINE_AV}", json={"v": True}, headers=auth
    )
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "dp_somente_leitura"
    assert _caixa(classe, "uuid-1").chamadas == []


# Why: str.isdigit() is true for the superscript two and int() refuses it, so this path used
# to answer 500 with a traceback in the log, which a session holder could repeat at will. A
# data point of the other product is refused by the same rule that refuses dp 999.
# Por que: str.isdigit() é verdadeiro para o dois sobrescrito e o int() o recusa, então este
# caminho respondia 500 com traceback no log, que quem tem sessão podia repetir à vontade. Um
# data point do outro produto é recusado pela mesma regra que recusa o dp 999.
@pytest.mark.parametrize("dpid", ["999", "abc", "0", "²", "١", "1" * 11, "-121", str(CENA_AR)])
async def test_um_data_point_fora_do_contrato_e_recusado(duas, dpid):
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/licencas/{AV}/dp/{dpid}", json={"v": 10}, headers=auth)
    assert resposta.status == 404, await resposta.text()
    assert (await _json(resposta))["code"] == "dp_desconhecido"
    assert _caixa(classe, "uuid-1").chamadas == []


@pytest.mark.parametrize("valor", [300, -1, True, "40", None, [40], {"v": 40}, 40.0])
async def test_um_valor_fora_do_tipo_do_data_point_e_recusado(duas, valor):
    """A value DP takes an integer of 0 to 100, and the JSON true is not one of them.

    Um DP value aceita um inteiro de 0 a 100, e o true do JSON não é um deles.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(
        f"/api/licencas/{AV}/dp/{NIVEL_1}", json={"v": valor}, headers=auth
    )
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "valor_invalido"
    assert _caixa(classe, "uuid-1").chamadas == []


async def test_um_set_num_numero_vazio_responde_numero_offline(abrir):
    cliente, auth = await abrir({TIPO: _fabrica()})
    resposta = await cliente.post(f"/api/licencas/{AV}/dp/{NIVEL_1}", json={"v": 40}, headers=auth)
    assert resposta.status == 503
    assert (await _json(resposta))["code"] == "numero_offline"


async def test_um_set_numa_licenca_que_nao_existe_e_licenca_desconhecida(duas):
    """The set takes the door of the bus, so it answers the code of the bus for a licence
    the bus does not know.

    O set toma a porta do barramento, então responde o código do barramento para uma licença
    que o barramento não conhece.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/licencas/zzz/dp/{NIVEL_1}", json={"v": 40}, headers=auth)
    assert resposta.status == 404
    assert (await _json(resposta))["code"] == "licenca_desconhecida"
    assert _caixa(classe, "uuid-1").chamadas == []


async def test_o_ligado_de_um_equipamento_always_on_e_nao_suportado(duas):
    """Section 14: a speaker that declares neither power capability has no switch, so the
    set is refused before the driver and the driver never writes a method only to say no.

    Seção 14: uma caixa que não declara nenhuma capacidade de energia não tem chave, então o
    set é recusado antes do driver e o driver nunca escreve método só para dizer não.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(
        f"/api/licencas/{AV}/dp/{LIGADO_1}", json={"v": True}, headers=auth
    )
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "nao_suportado"
    assert _caixa(classe, "uuid-1").chamadas == []


async def test_o_ligado_de_um_projetor_chega_como_ligar_e_desligar(abrir):
    """Section 6: any registered equipment occupies a number, so a projector saved in number
    2 boots in number 2 and its power data point is the pair of power capabilities.

    Seção 6: qualquer equipamento cadastrado ocupa um número, então um projetor salvo no
    número 2 sobe no número 2 e o data point de ligar dele é o par de capacidades de energia.
    """
    projetor = _projetor()
    cliente, auth = await abrir(
        {TIPO: _fabrica(), TIPO_DE_PROJETOR: projetor},
        equipamentos=(
            _cadastro("uuid-1", ip=IP_1, nome="Sala"),
            _cadastro("uuid-2", tipo=TIPO_DE_PROJETOR, ip=IP_2, nome="Projetor"),
        ),
        numeros={AV: ("uuid-1", "uuid-2")},
    )
    numeros = (await _licenca(cliente, auth))["numeros"]
    assert [numero["identidade"] for numero in numeros[:2]] == ["uuid-1", "uuid-2"]
    dps = (await _dps(cliente, auth))["dps"]
    assert dps[str(LIGADO_2)] is False
    assert str(LIGADO_1) not in dps
    for valor, acao in ((True, "ligar"), (False, "desligar")):
        resposta = await cliente.post(
            f"/api/licencas/{AV}/dp/{LIGADO_2}", json={"v": valor}, headers=auth
        )
        assert resposta.status == 200, await resposta.text()
        assert _caixa(projetor, "uuid-2").chamadas[-1] == (acao, None)


async def test_os_data_points_de_um_ar_chegam_como_as_acoes_da_secao_6(ar):
    cliente, auth, classe = ar
    pedidos = (
        (TEMPERATURA_AR_1, 24, ("temperatura", 24)),
        (MODO_AR_1, "frio", ("modo", "frio")),
        (VENTO_AR_1, "alto", ("vento", "alto")),
        (LIGADO_AR_1, False, ("desligar", None)),
        (LIGADO_AR_1, True, ("ligar", None)),
    )
    for dpid, valor, esperado in pedidos:
        resposta = await cliente.post(
            f"/api/licencas/{AR}/dp/{dpid}", json={"v": valor}, headers=auth
        )
        assert resposta.status == 200, await resposta.text()
        assert _caixa(classe, "uuid-ar").chamadas[-1] == esperado


@pytest.mark.parametrize(
    ("dpid", "valor"),
    [(MODO_AR_1, "quente"), (MODO_AR_1, "gelado"), (VENTO_AR_1, "baixo"), (TEMPERATURA_AR_1, 31)],
)
async def test_uma_palavra_que_a_maquina_nao_fala_e_recusada_antes_do_driver(ar, dpid, valor):
    """Section 6: a mode of the enum of section 8 that the manifest does not declare never
    reaches the driver, so no driver writes a check only to say no.

    Seção 6: um modo do enum da seção 8 que o manifesto não declara nunca chega ao driver,
    então nenhum driver escreve conferência só para dizer não.
    """
    cliente, auth, classe = ar
    resposta = await cliente.post(f"/api/licencas/{AR}/dp/{dpid}", json={"v": valor}, headers=auth)
    assert resposta.status == 400, await resposta.text()
    assert (await _json(resposta))["code"] == "valor_invalido"
    assert _caixa(classe, "uuid-ar").chamadas == []


async def test_o_canal_de_comando_chega_pela_rota_como_uma_capacidade(duas):
    """Section 8: the panel writes n:acao[:valor] and the hub turns it into one capability
    on the equipment of number n, refusing what the manifest does not declare.

    Seção 8: o painel escreve n:acao[:valor] e o hub o transforma numa capacidade no
    equipamento do número n, recusando o que o manifesto não declara.
    """
    cliente, auth, classe = duas
    caminho = f"/api/licencas/{AV}/dp/{COMANDO}"
    resposta = await cliente.post(caminho, json={"v": "2:tocar"}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert _caixa(classe, "uuid-2").chamadas == [("tocar", None)]
    assert _caixa(classe, "uuid-1").chamadas == []
    resposta = await cliente.post(caminho, json={"v": "1:mudo"}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert _caixa(classe, "uuid-1").chamadas == [("mudo", True)]
    for texto, status, codigo in (
        ("3:tocar", 503, "numero_offline"),
        ("1:ligar", 400, "nao_suportado"),
        ("1:entrada:1", 400, "valor_invalido"),
        ("1:dancar", 400, "valor_invalido"),
    ):
        resposta = await cliente.post(caminho, json={"v": texto}, headers=auth)
        assert resposta.status == status, texto
        assert (await _json(resposta))["code"] == codigo, texto
    assert _caixa(classe, "uuid-1").chamadas == [("mudo", True)]


async def test_o_dp_de_cena_responde_o_mesmo_que_a_rota_de_cenas(duas):
    """Section 11: DP 141 runs a scene, so it answers the codes of the scene executor and not
    a 500 with a traceback for a scene that simply does not exist.

    Seção 11: o DP 141 executa uma cena, então ele responde os códigos do executor de cenas e
    não um 500 com traceback para uma cena que simplesmente não existe.
    """
    cliente, auth, _classe = duas
    resposta = await cliente.post(f"/api/licencas/{AV}/dp/{CENA_AV}", json={"v": 1}, headers=auth)
    assert resposta.status == 404, await resposta.text()
    assert (await _json(resposta))["code"] == "cena_nao_encontrada"
    for valor in ("cena1", 0, 33, True):
        resposta = await cliente.post(
            f"/api/licencas/{AV}/dp/{CENA_AV}", json={"v": valor}, headers=auth
        )
        assert resposta.status == 400, valor
        assert (await _json(resposta))["code"] == "valor_invalido", valor


async def test_o_dp_de_cena_de_uma_licenca_de_ar_e_a_mesma_cena(ar):
    """The same number is the same scene in every licence, section 8.

    O mesmo número é a mesma cena em toda licença, seção 8.
    """
    cliente, auth, _classe = ar
    resposta = await cliente.post(f"/api/licencas/{AR}/dp/{CENA_AR}", json={"v": 1}, headers=auth)
    assert resposta.status == 404, await resposta.text()
    assert (await _json(resposta))["code"] == "cena_nao_encontrada"


def test_todo_codigo_do_executor_de_cenas_tem_status_no_dp_de_cena():
    """The other executor code reaches DP 141 the same way, and a code with no status here is
    a 500 with erro_interno for something the scenes route answers honestly.

    O outro código do executor chega ao DP 141 do mesmo jeito, e um código sem status aqui é
    um 500 com erro_interno para algo que a rota de cenas responde honestamente.
    """
    from iphub import cenas as modulo_cenas
    from iphub.api.licencas import STATUS_POR_CODIGO

    assert STATUS_POR_CODIGO[modulo_cenas.CENA_NAO_ENCONTRADA] == 404
    assert STATUS_POR_CODIGO[modulo_cenas.CENA_EM_CURSO] == 409


async def test_o_grupo_se_forma_pelo_mestre_e_cai_pelo_mestre(duas):
    """Section 14: the slave joins the master, and only the master takes the group down.

    Seção 14: o escravo entra no mestre, e só o mestre desfaz o grupo.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": 1}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert (await _json(resposta))["grupo"] == 1
    assert _caixa(classe, "uuid-2").chamadas == [("entrar_no_grupo", IP_1)]
    licenca = await _licenca(cliente, auth)
    assert licenca["grupo"] == 1
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["mestre", "escravo"]
    assert (await _dps(cliente, auth))["dps"][str(GRUPO)] == 1
    resposta = await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": 0}, headers=auth)
    assert (await _json(resposta))["grupo"] == 0
    assert ("desfazer_grupo", None) in _caixa(classe, "uuid-1").chamadas
    assert ("desfazer_grupo", None) not in _caixa(classe, "uuid-2").chamadas


async def test_um_grupo_de_uma_caixa_so_nao_e_grupo(abrir):
    cliente, auth = await abrir(
        {TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),), numeros={AV: ("uuid-1",)}
    )
    resposta = await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": 1}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "nao_suportado"


@pytest.mark.parametrize("valor", [13, -1, "grupo1", True, None, 1.0])
async def test_um_grupo_de_um_numero_que_o_contrato_nao_tem_e_recusado(duas, valor):
    """DP 142 carries the NUMBER of the master, and section 8 numbers twelve of them.

    O DP 142 leva o NÚMERO do mestre, e a seção 8 numera doze deles.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": valor}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "valor_invalido"
    assert _caixa(classe, "uuid-2").chamadas == []


async def test_um_grupo_misto_nunca_e_oferecido(abrir):
    """Section 14: a group only ever exists between speakers of the same domain.

    Seção 14: um grupo só existe entre caixas do mesmo domínio.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica(), OUTRO_TIPO: _fabrica(OUTRO_TIPO)},
        equipamentos=(
            _cadastro("uuid-1"),
            _cadastro("uuid-outra", tipo=OUTRO_TIPO, ip=IP_2, nome="Quarto"),
        ),
        numeros={AV: ("uuid-1", "uuid-outra")},
    )
    resposta = await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": 1}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "nao_suportado"


async def test_uma_licenca_de_ar_nao_forma_grupo(abrir):
    """Section 8: the group is of the licence of audio and video; a machine of air has no
    group data point and its number never carries a role.

    Seção 8: o grupo é da licença de áudio e vídeo; uma máquina de ar não tem data point de
    grupo e o número dela nunca leva papel.
    """
    cliente, auth = await abrir(
        {TIPO_DE_AR: _ar()},
        equipamentos=(
            _cadastro("uuid-ar-1", tipo=TIPO_DE_AR, ip=IP_1, nome="Sala"),
            _cadastro("uuid-ar-2", tipo=TIPO_DE_AR, ip=IP_2, nome="Quarto"),
        ),
        licencas=(LICENCA_AR,),
        numeros={AR: ("uuid-ar-1", "uuid-ar-2")},
    )
    resposta = await cliente.post(f"/api/licencas/{AR}/grupo", json={"v": 1}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "nao_suportado"
    licenca = await _licenca(cliente, auth, AR)
    assert licenca["grupo"] == 0
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["", ""]


async def test_remover_um_equipamento_esvazia_o_numero_e_nao_empurra_o_resto(duas, amb):
    """A shift would move the speaker of number 2 into number 1 in every automation.

    Um empurrão moveria a caixa do número 2 para o número 1 em toda automação.
    """
    cliente, auth, _classe = duas
    resposta = await cliente.delete("/api/equipamentos/uuid-1", headers=auth)
    assert resposta.status == 200, await resposta.text()
    numeros = (await _licenca(cliente, auth))["numeros"]
    assert [numero["identidade"] for numero in numeros[:2]] == ["", "uuid-2"]
    assert _em_disco(amb)["numeros"] == {AV: ["", "uuid-2"]}


async def test_remover_o_mestre_derruba_o_grupo(duas):
    """A group led by an equipment nobody has is a group nobody can take down.

    Um grupo liderado por um equipamento que ninguém tem é um grupo que ninguém desfaz.
    """
    cliente, auth, _classe = duas
    assert (
        await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": 1}, headers=auth)
    ).status == 200
    assert (await cliente.delete("/api/equipamentos/uuid-1", headers=auth)).status == 200
    licenca = await _licenca(cliente, auth)
    assert licenca["grupo"] == 0
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["", ""]


async def test_tirar_o_mestre_da_ordem_derruba_o_grupo_pelo_mestre(duas):
    """Section 14: the group is taken down while the OLD order still reaches the master;
    rewriting the order first would leave the speakers physically grouped forever while the
    hub publishes solo.

    Seção 14: o grupo cai enquanto a ordem ANTIGA ainda alcança o mestre; reescrever a ordem
    antes deixaria as caixas fisicamente agrupadas para sempre enquanto o hub publica solo.
    """
    cliente, auth, classe = duas
    assert (
        await cliente.post(f"/api/licencas/{AV}/grupo", json={"v": 1}, headers=auth)
    ).status == 200
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": ["", "uuid-2"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    assert ("desfazer_grupo", None) in _caixa(classe, "uuid-1").chamadas
    licenca = await _licenca(cliente, auth)
    assert licenca["grupo"] == 0
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["", ""]


async def test_um_numero_escravo_de_grupo_alheio_nao_e_desenhado_como_solo(duas):
    """The panel draws no role badge for a solo number, so calling this one solo left the
    operator with volume, transport and input controls that only ever answer no.

    O painel não desenha selo de papel para um número solo, então chamar este de solo deixava
    o operador com controles de volume, transporte e entrada que só respondem não.
    """
    cliente, auth, classe = duas
    _caixa(classe, "uuid-1").escravo_alheio = True
    licenca = await _licenca(cliente, auth)
    assert [numero["papel"] for numero in licenca["numeros"][:2]] == ["escravo", ""]


async def test_um_corpo_json_fundo_demais_e_corpo_invalido_e_nunca_erro_interno(duas):
    """Section 11: a body this daemon cannot read answers a stable code, never a 500 with a
    traceback in the log.

    Seção 11: um corpo que este daemon não consegue ler responde um código estável, nunca um
    500 com traceback no log.
    """
    cliente, auth, _classe = duas
    # Why: the scenes route takes a body of 256 kB, so a body deep enough to exhaust the
    # recursion of the parser fits inside its ceiling and really reaches json.loads.
    # Por que: a rota de cenas aceita corpo de 256 kB, então um corpo fundo o bastante para
    # esgotar a recursão do parser cabe no teto dela e chega mesmo ao json.loads.
    fundo = "[" * 12000 + "]" * 12000
    resposta = await cliente.post(
        "/api/cenas",
        data=fundo,
        headers={**auth, "Content-Type": "application/json"},
    )
    assert resposta.status != 500, await resposta.text()
    assert (await _json(resposta))["code"] == "corpo_invalido"


async def test_o_livro_de_licencas_tem_teto(abrir, amb):
    """Every licence is a device the bridge serves and a slice the bus walks every second, so
    whoever holds a session cannot fill config.json with licences.

    Toda licença é um dispositivo que a ponte serve e uma fatia que o barramento percorre a
    cada segundo, então quem tem sessão não consegue encher o config.json de licenças.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, licencas=())
    for _ in range(rotas.LICENCAS_MAXIMO):
        resposta = await cliente.post("/api/licencas", json={"produto": "av"}, headers=auth)
        assert resposta.status == 200, await resposta.text()
    resposta = await cliente.post("/api/licencas", json={"produto": "ar"}, headers=auth)
    assert resposta.status == 409
    assert await _json(resposta) == {"ok": False, "code": "licencas_demais"}
    assert len(_em_disco(amb)["licencas"]) == rotas.LICENCAS_MAXIMO


async def test_trocar_o_tipo_para_o_outro_produto_com_numero_e_recusado(abrir, amb):
    """Section 8, an equipment only enters a licence of its product, so a speaker on number 1
    of a licence of audio and video cannot become an air conditioner while it holds the
    number; the number would be emptied in silence on the next boot.

    Seção 8, um equipamento só entra numa licença do produto dele, então uma caixa no número 1
    de uma licença de áudio e vídeo não pode virar ar condicionado enquanto ocupa o número; o
    número seria esvaziado em silêncio no próximo boot.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica(), TIPO_DE_AR: _ar()},
        equipamentos=(_cadastro("uuid-1", ip=IP_1, nome="Sala"),),
        numeros={AV: ("uuid-1",)},
    )
    corpo = {"tipo": TIPO_DE_AR, "identidade": "uuid-1", "nome": "Sala", "ip": IP_1, "campos": {}}
    resposta = await cliente.post("/api/equipamentos/uuid-1", json=corpo, headers=auth)
    assert resposta.status == 400
    assert await _json(resposta) == {"ok": False, "code": "produto_incompativel"}
    assert _em_disco(amb)["equipamentos"][0]["tipo"] == TIPO
    assert (await _licenca(cliente, auth))["numeros"][0]["identidade"] == "uuid-1"
