# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9 over the licence and scene routes, with every test attacking a rule.

Nobody without a session commands a number, nobody from another site commands one either,
and the four headers are on every answer, refusals included. Past the gate the attacks are
the ones sections 8 and 9 pay for: the chave of a licence leaking back in any answer, a
licence id that tries to be a path, an order that is not a list of registered identities, an
identity carrying a control character, a scene step that runs what nobody runs, and a scene
name that does not fit the 255 bytes of DP 154. Each one is checked on the FILE as well,
because a refusal that already wrote is not a refusal.

Seção 9 sobre as rotas de licença e de cena, com todo teste atacando uma regra.

Ninguém sem sessão comanda um número, ninguém de outro site comanda também, e os quatro
cabeçalhos estão em toda resposta, inclusive nas recusas. Passado o portão, os ataques são os
que as seções 8 e 9 pagam: a chave de uma licença vazando de volta em qualquer resposta, um
id de licença que tenta ser caminho, uma ordem que não é lista de identidades cadastradas,
uma identidade com caractere de controle, um passo de cena que roda o que ninguém roda, e um
nome de cena que não cabe nos 255 bytes do DP 154. Cada um é conferido também no ARQUIVO,
porque uma recusa que já gravou não é recusa.
"""

import json

import pytest

from iphub.api.cenas import CORPO_MAXIMO_CENAS
from iphub.api.comum import CORPO_MAXIMO
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto
from iphub.portao import CABECALHOS

TIPO = "multiroom_falso"
TIPO_DE_PROJETOR = "projetor_falso"

AV = "av1"
CHAVE = "chave-secreta-da-tuya-so-deste-teste"
UUID = "uuid-da-tuya"
PID = "pid123"

# The numbers of section 8, written by hand on purpose.
# Os números da seção 8, escritos na mão de propósito.
NIVEL_1 = 121
CENA = 141
ONLINE = 144
NOMES_CENAS = 154

ALHEIA = "http://evil.example.com"

ROTAS = (
    ("GET", "/api/licencas"),
    ("POST", "/api/licencas"),
    ("POST", f"/api/licencas/{AV}"),
    ("DELETE", f"/api/licencas/{AV}"),
    ("POST", f"/api/licencas/{AV}/numeros"),
    ("GET", f"/api/licencas/{AV}/dps"),
    ("POST", f"/api/licencas/{AV}/dp/{NIVEL_1}"),
    ("POST", f"/api/licencas/{AV}/grupo"),
    ("GET", f"/api/licencas/{AV}/qr"),
    ("GET", "/api/cenas"),
    ("POST", "/api/cenas"),
    ("POST", "/api/cenas/1/executar"),
)

# The routes that take a licence id, with the code each answers for an id nobody has; the
# set takes the door of the bus and speaks its vocabulary.
# As rotas que recebem id de licença, com o código que cada uma responde para um id que
# ninguém tem; o set toma a porta do barramento e fala o vocabulário dele.
ROTAS_COM_ID = (
    ("POST", "/api/licencas/{id}", {"nome": "x"}, "licenca_nao_encontrada"),
    ("DELETE", "/api/licencas/{id}", None, "licenca_nao_encontrada"),
    ("POST", "/api/licencas/{id}/numeros", {"numeros": []}, "licenca_nao_encontrada"),
    ("GET", "/api/licencas/{id}/dps", None, "licenca_nao_encontrada"),
    ("POST", "/api/licencas/{id}/dp/" + str(NIVEL_1), {"v": 40}, "licenca_desconhecida"),
    ("POST", "/api/licencas/{id}/grupo", {"v": 0}, "licenca_nao_encontrada"),
    ("GET", "/api/licencas/{id}/qr", None, "licenca_nao_encontrada"),
)

IDS_QUE_NINGUEM_TEM = (
    "zzz",
    "AV1",
    "%20av1",
    "..%2Fav1",
    "%2E%2E%2F%2E%2E%2Fapi%2Festado",
    "av1%00",
    "av1%2F1",
    "a" * 1000,
    "²",
)


def _manifesto(tipo: str, categoria: str, capacidades: tuple[str, ...]) -> Manifesto:
    textos = {"descricao": "Aparelho de teste"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Aparelho", "en": "Device"},
        categoria=categoria,
        capacidades=capacidades,
        textos={"pt": dict(textos), "en": dict(textos)},
    )


class Caixa(Driver):
    """A multiroom driver that records what reached it, so a test proves what never did.

    Um driver multiroom que guarda o que chegou nele, para um teste provar o que nunca chegou.
    """

    MANIFESTO = _manifesto(TIPO, "multiroom", ("volume", "fonte", "tocar", "pausar", "agrupar"))
    instancias: list["Caixa"] = []

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self.chamadas: list[tuple[str, object]] = []
        self._defina(online=True, volume=20, fonte="wifi", fontes=("wifi",))
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

    async def ler_grupo(self) -> None:
        return None

    def marcar_grupo(self, dentro: bool) -> None:
        return None

    def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
        return None

    def e_escravo(self) -> bool:
        return False

    def saiu_do_grupo(self) -> bool:
        return False


class Projetor(Driver):
    """Not a multiroom equipment: it takes a number of section 8 like any other, never a
    group.

    Não é equipamento multiroom: ocupa um número da seção 8 como qualquer outro, nunca um
    grupo.
    """

    MANIFESTO = _manifesto(TIPO_DE_PROJETOR, "projetor", ("ligar", "desligar"))

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._defina(online=True, ligado=False)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        return None


CATALOGO = {TIPO: Caixa, TIPO_DE_PROJETOR: Projetor}
EQUIPAMENTOS = (
    Cadastro(identidade="uuid-1", tipo=TIPO, nome="Sala", ip="192.0.2.11"),
    Cadastro(identidade="uuid-projetor", tipo=TIPO_DE_PROJETOR, nome="Sala", ip="192.0.2.20"),
)
LICENCA_EM_DISCO = {
    "id": AV,
    "produto": "av",
    "nome": "Casa",
    "uuid": UUID,
    "pid": PID,
    "chave": CHAVE,
}
NUMEROS = {AV: ("uuid-1",)}


@pytest.fixture
async def hub(fabrica_cliente, posse, bearer):
    """The licence and its one number are created through the routes, with the credential
    typed the way the integrator types it.

    A licença e o número dela são criados pelas rotas, com a credencial digitada do jeito que
    o integrador a digita.
    """
    Caixa.instancias = []
    cliente = await fabrica_cliente(catalogo=CATALOGO, config=Config(equipamentos=EQUIPAMENTOS))
    auth = bearer(await posse(cliente))
    corpo = {"produto": "av", "id": AV, "nome": "Casa", "uuid": UUID, "pid": PID, "chave": CHAVE}
    resposta = await cliente.post("/api/licencas", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": list(NUMEROS[AV])}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    return cliente, auth


def _do_disco(amb) -> dict:
    caminho = amb.dir_data / ARQUIVO_CONFIG
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.is_file() else {}


def _caixa() -> Caixa:
    return Caixa.instancias[0]


def _confere_cabecalhos(resposta) -> None:
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome


def _confere_intacto(amb) -> None:
    """The licence, its numbers and the scenes exactly as the hub booted with them.

    A licença, os números dela e as cenas exatamente como o hub subiu com eles.
    """
    em_disco = _do_disco(amb)
    assert em_disco["licencas"] == [LICENCA_EM_DISCO]
    assert em_disco["numeros"] == {AV: ["uuid-1"]}
    assert em_disco.get("cenas", []) == []


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_nenhuma_rota_de_licenca_responde_sem_sessao(hub, amb, metodo, caminho):
    cliente, _auth = hub
    resposta = await cliente.request(metodo, caminho, json={"v": 40})
    assert resposta.status == 401
    assert (await resposta.json())["ok"] is False
    _confere_cabecalhos(resposta)
    assert _caixa().chamadas == []
    _confere_intacto(amb)


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_token_inventado_nao_abre_nenhuma_rota_de_licenca(hub, amb, bearer, metodo, caminho):
    cliente, _auth = hub
    resposta = await cliente.request(
        metodo, caminho, json={"v": 40}, headers=bearer("token-que-ninguem-emitiu")
    )
    assert resposta.status == 401
    assert (await resposta.json())["ok"] is False
    assert _caixa().chamadas == []
    _confere_intacto(amb)


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_nenhuma_rota_de_licenca_responde_a_outro_site(hub, amb, metodo, caminho):
    """Section 9: a present Origin that is not this host is 403, which closes CSRF.

    Seção 9: um Origin presente que não é este host é 403, o que fecha o CSRF.
    """
    cliente, auth = hub
    resposta = await cliente.request(
        metodo, caminho, json={"v": 40}, headers={**auth, "Origin": ALHEIA}
    )
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "origem_nao_permitida"}
    assert _caixa().chamadas == []
    _confere_intacto(amb)


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_os_quatro_cabecalhos_estao_na_resposta_com_sessao(hub, metodo, caminho):
    cliente, auth = hub
    resposta = await cliente.request(metodo, caminho, json={"v": 40}, headers=auth)
    _confere_cabecalhos(resposta)


async def test_a_chave_da_licenca_nunca_volta_em_resposta_nenhuma(hub, amb):
    """Section 9: the chave is the credential of the device on the platform, so it is
    written and never read back, by no route of the daemon, the public one included.

    Seção 9: a chave é a credencial do dispositivo na plataforma, então ela é escrita e nunca
    lida de volta, por rota nenhuma do daemon, a pública inclusive.
    """
    cliente, auth = hub
    # Why: the file really holds the credential, otherwise this test would prove nothing.
    # Por que: o arquivo guarda mesmo a credencial, senão este teste não provaria nada.
    assert _do_disco(amb)["licencas"][0]["chave"] == CHAVE
    de_ar = {"produto": "ar", "uuid": "uuid-do-ar", "pid": "pid-do-ar", "chave": CHAVE}
    pedidos = (
        ("GET", "/api/estado", None, {}),
        ("GET", "/api/licencas", None, auth),
        ("POST", "/api/licencas", de_ar, auth),
        ("POST", f"/api/licencas/{AV}", {"nome": "Outra"}, auth),
        ("POST", f"/api/licencas/{AV}/numeros", {"numeros": ["uuid-1"]}, auth),
        ("GET", f"/api/licencas/{AV}/dps", None, auth),
        ("POST", f"/api/licencas/{AV}/dp/{NIVEL_1}", {"v": 30}, auth),
        ("POST", f"/api/licencas/{AV}/grupo", {"v": 0}, auth),
        ("GET", f"/api/licencas/{AV}/qr", None, auth),
        ("GET", "/api/equipamentos", None, auth),
        ("GET", "/api/catalogo", None, auth),
        ("GET", "/api/cenas", None, auth),
        # Why: the removal goes last, so every other route answers about a licence that is
        # still there and really holds the credential.
        # Por que: a remoção fica por último, para toda outra rota responder sobre uma licença
        # que ainda está lá e de fato guarda a credencial.
        ("DELETE", f"/api/licencas/{AV}", None, auth),
        ("DELETE", "/api/licencas/ar1", None, auth),
    )
    corpos = []
    for metodo, caminho, corpo, cabecalhos in pedidos:
        resposta = await cliente.request(metodo, caminho, json=corpo, headers=cabecalhos)
        assert resposta.status == 200, f"{metodo} {caminho} {await resposta.text()}"
        corpos.append(f"{metodo} {caminho} {await resposta.text()}")
    ofensores = [texto for texto in corpos if CHAVE in texto]
    assert not ofensores, ofensores
    assert '"chave"' not in "".join(corpos)


@pytest.mark.parametrize("bruto", IDS_QUE_NINGUEM_TEM)
async def test_uma_licenca_que_ninguem_tem_e_404_e_nunca_500(hub, amb, bruto):
    """The id is a path segment typed by whoever holds a session, so a dot dot, an encoded
    slash, a NUL or a thousand characters are a licence nobody has, never a second path and
    never a traceback.

    O id é segmento de caminho digitado por quem tem sessão, então um ponto ponto, uma barra
    codificada, um NUL ou mil caracteres são uma licença que ninguém tem, nunca outro caminho
    e nunca um traceback.
    """
    cliente, auth = hub
    for metodo, modelo, corpo, codigo in ROTAS_COM_ID:
        caminho = modelo.replace("{id}", bruto)
        resposta = await cliente.request(metodo, caminho, json=corpo, headers=auth)
        assert resposta.status == 404, f"{metodo} {caminho} {await resposta.text()}"
        assert await resposta.json() == {"ok": False, "code": codigo}, f"{metodo} {caminho}"
    assert _caixa().chamadas == []
    _confere_intacto(amb)


@pytest.mark.parametrize("ordem", [["uuid-9"], "uuid-1", [1], [["uuid-1"]], {"1": "uuid-1"}, None])
async def test_uma_ordem_que_nao_e_lista_de_identidade_e_recusada(hub, amb, ordem):
    cliente, auth = hub
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": ordem}, headers=auth
    )
    assert resposta.status in (400, 404)
    assert (await resposta.json())["ok"] is False
    _confere_intacto(amb)


@pytest.mark.parametrize(
    "identidade", ["uuid-1\x00", "\x1b[31muuid-1", "uuid\x7f-1", "uuid-1\x00uuid-1", "uuid-1\u200b"]
)
async def test_uma_identidade_com_caractere_de_controle_nao_ocupa_numero(hub, amb, identidade):
    """An identity is a uuid, a MAC or a serial, and one that carries a control character is
    not a registered equipment; it never reaches the file, where it would break the JSON of
    the bus.

    Uma identidade é uuid, MAC ou serial, e uma que leva caractere de controle não é
    equipamento cadastrado; ela nunca chega ao arquivo, onde quebraria o JSON do barramento.
    """
    cliente, auth = hub
    resposta = await cliente.post(
        f"/api/licencas/{AV}/numeros", json={"numeros": [identidade]}, headers=auth
    )
    assert resposta.status == 404, await resposta.text()
    assert await resposta.json() == {"ok": False, "code": "eq_nao_encontrado"}
    _confere_intacto(amb)


@pytest.mark.parametrize(
    "corpo",
    [
        {"nome": "Casa\x00"},
        {"uuid": "uuid\x1b[31m"},
        {"pid": "pid\n"},
        {"chave": "chave\x7f"},
        {"uuid": "uuid-ç"},
        {"nome": "N" * 41},
        {"uuid": "u" * 65},
        {"chave": "c" * 129},
    ],
)
async def test_um_campo_de_licenca_com_controle_ou_fora_do_teto_nao_chega_ao_arquivo(
    hub, amb, corpo
):
    """What is typed here ends up in config.json and in the QR code, so a control character,
    a letter outside ASCII in an identifier and a paragraph are refused where they are typed.

    O que se digita aqui termina no config.json e no QR code, então um caractere de controle,
    uma letra fora do ASCII num identificador e um parágrafo são recusados onde são digitados.
    """
    cliente, auth = hub
    resposta = await cliente.post(f"/api/licencas/{AV}", json=corpo, headers=auth)
    assert resposta.status == 400, await resposta.text()
    assert await resposta.json() == {"ok": False, "code": "licenca_invalida"}
    _confere_intacto(amb)


async def test_um_uuid_nao_injeta_parametro_no_qr(hub):
    """The uuid and the pid travel inside the URL of the QR code, so the characters that
    mean something in a URL are encoded and never become a second parameter.

    O uuid e o pid viajam dentro da URL do QR code, então os caracteres que significam algo
    numa URL são codificados e nunca viram um segundo parâmetro.
    """
    cliente, auth = hub
    corpo = {"uuid": "x&v=1.0&uuid=outro", "pid": "p?q#r"}
    resposta = await cliente.post(f"/api/licencas/{AV}", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    resposta = await cliente.get(f"/api/licencas/{AV}/qr", headers=auth)
    assert resposta.status == 200, await resposta.text()
    conteudo = (await resposta.json())["conteudo"]
    assert (
        conteudo
        == "https://smartapp.tuya.com/s/p?p=p%3Fq%23r&uuid=x%26v%3D1.0%26uuid%3Doutro&v=2.0"
    )


@pytest.mark.parametrize("acao", ["online", "agrupar", "perfis", "nomes", "grupo_de_todos"])
async def test_um_passo_de_cena_nao_roda_o_que_ninguem_roda(hub, amb, acao):
    """Section 8: a step is one capability of section 6 or the group, and a report is only
    ever born of real state, so a scene never writes one.

    Seção 8: um passo é uma capacidade da seção 6 ou o grupo, e um report só nasce de estado
    real, então uma cena nunca escreve um.
    """
    cliente, auth = hub
    passo = {"equipamento": "uuid-1", "acao": acao, "valor": True}
    corpo = {"cenas": [{"nome": "Filme", "passos": [passo]}]}
    resposta = await cliente.post("/api/cenas", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "cenas_invalidas"
    codigos = [problema["codigo"] for problema in (await resposta.json())["problemas"]]
    assert codigos == ["cena_acao_desconhecida"]
    _confere_intacto(amb)


async def test_uma_cena_nao_dispara_uma_cena(hub, amb):
    """Two scenes naming each other would be a hub that never stops.

    Duas cenas nomeando uma à outra seriam um hub que nunca para.
    """
    cliente, auth = hub
    passo = {"equipamento": "uuid-1", "acao": "cena", "valor": 1}
    corpo = {"cenas": [{"nome": "Laco", "passos": [passo]}]}
    resposta = await cliente.post("/api/cenas", json=corpo, headers=auth)
    assert resposta.status == 400
    codigos = [problema["codigo"] for problema in (await resposta.json())["problemas"]]
    assert codigos == ["cena_acao_desconhecida"]
    _confere_intacto(amb)


async def test_um_nome_de_cena_que_nao_cabe_no_dp_154_e_recusado(hub, amb):
    cliente, auth = hub
    passo = {"equipamento": "uuid-1", "acao": "volume", "valor": 10}
    cena = {"nome": "N" * 40, "passos": [passo]}
    resposta = await cliente.post("/api/cenas", json={"cenas": [cena] * 8}, headers=auth)
    assert resposta.status == 400
    codigos = [problema["codigo"] for problema in (await resposta.json())["problemas"]]
    assert "nomes_longos" in codigos
    _confere_intacto(amb)


async def test_um_set_de_data_point_de_report_nunca_alcanca_a_caixa(hub):
    cliente, auth = hub
    resposta = await cliente.post(f"/api/licencas/{AV}/dp/{ONLINE}", json={"v": True}, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "dp_somente_leitura"
    assert _caixa().chamadas == []


@pytest.mark.parametrize("valor", [0, 33, 10**12, -(10**12), "1", True])
async def test_um_numero_de_cena_fora_do_contrato_nunca_e_erro_interno(hub, valor):
    cliente, auth = hub
    resposta = await cliente.post(f"/api/licencas/{AV}/dp/{CENA}", json={"v": valor}, headers=auth)
    assert resposta.status == 400, await resposta.text()
    assert await resposta.json() == {"ok": False, "code": "valor_invalido"}


async def test_um_corpo_maior_que_o_teto_da_rota_e_recusado(hub, amb):
    """A body that does not fit is refused whole, and nothing of it is saved.

    Um corpo que não cabe é recusado inteiro, e nada dele é gravado.
    """
    cliente, auth = hub
    passo = {"equipamento": "uuid-1", "acao": "volume", "valor": 10, "espera_ms": 0}
    cena = {"nome": "Filme", "passos": [passo]}
    sobra = "a" * CORPO_MAXIMO
    gigantes = (
        ("/api/cenas", {"cenas": [cena], "sobra": "a" * CORPO_MAXIMO_CENAS}),
        ("/api/licencas", {"produto": "ar", "nome": "Outra", "sobra": sobra}),
        (f"/api/licencas/{AV}", {"nome": "Outra", "sobra": sobra}),
        (f"/api/licencas/{AV}/numeros", {"numeros": [], "sobra": sobra}),
        (f"/api/licencas/{AV}/grupo", {"v": 0, "sobra": sobra}),
        (f"/api/licencas/{AV}/dp/{NIVEL_1}", {"v": 40, "sobra": sobra}),
    )
    for caminho, corpo in gigantes:
        resposta = await cliente.post(
            caminho,
            data=json.dumps(corpo),
            headers={**auth, "Content-Type": "application/json"},
        )
        assert resposta.status == 400, caminho
        assert (await resposta.json())["code"] == "corpo_invalido", caminho
    assert _caixa().chamadas == []
    _confere_intacto(amb)


async def test_um_corpo_que_nao_e_objeto_nao_comanda_nada(hub, amb):
    cliente, auth = hub
    caminhos = (
        f"/api/licencas/{AV}/dp/{NIVEL_1}",
        "/api/licencas",
        f"/api/licencas/{AV}",
        f"/api/licencas/{AV}/numeros",
        f"/api/licencas/{AV}/grupo",
    )
    for caminho in caminhos:
        for bruto in ("[]", "40", '"40"', "null", "{", '{"v": 40', "\xff"):
            resposta = await cliente.post(
                caminho,
                data=bruto,
                headers={**auth, "Content-Type": "application/json"},
            )
            assert resposta.status == 400, f"{caminho} {bruto!r}"
            assert await resposta.json() == {"ok": False, "code": "corpo_invalido"}, (
                f"{caminho} {bruto!r}"
            )
    assert _caixa().chamadas == []
    _confere_intacto(amb)
