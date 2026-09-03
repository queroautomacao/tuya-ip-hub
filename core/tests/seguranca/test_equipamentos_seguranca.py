# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9 over the equipment routes: the session, the ip literal and the device secret.

Every test here attacks a rule: it tries to command the hub without a session, to make the
daemon reach a host nobody registered, to read back a credential of a device, and to make a
driver run an action its manifest does not declare.

Seção 9 sobre as rotas de equipamento: a sessão, o ip literal e o segredo do aparelho.

Todo teste aqui ataca uma regra: tenta comandar o hub sem sessão, fazer o daemon alcançar
um host que ninguém cadastrou, ler de volta uma credencial de aparelho e fazer um driver
executar uma ação que o manifesto dele não declara.
"""

import json

import pytest

from iphub.api.comum import CORPO_MAXIMO
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config, ip_literal
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Auth, Campo, Manifesto, TipoCampo
from iphub.portao import CABECALHOS

TIPO = "exemplo"
SENHA_DO_APARELHO = "credencial-do-projetor"
IDENTIDADE = "uuid-1"

CORPO = {
    "tipo": TIPO,
    "identidade": IDENTIDADE,
    "nome": "Sala",
    "ip": "192.0.2.10",
    "campos": {"porta": "8080", "senha": SENHA_DO_APARELHO},
}

ROTAS = (
    ("GET", "/api/catalogo"),
    ("GET", "/api/equipamentos"),
    ("POST", "/api/equipamentos"),
    ("POST", f"/api/equipamentos/{IDENTIDADE}"),
    ("DELETE", f"/api/equipamentos/{IDENTIDADE}"),
    ("POST", f"/api/equipamentos/{IDENTIDADE}/acao"),
    ("POST", f"/api/equipamentos/{IDENTIDADE}/autenticar"),
    ("POST", "/api/descoberta"),
)

MANIFESTO = Manifesto(
    tipo=TIPO,
    rotulo={"pt": "Exemplo", "en": "Example"},
    categoria="outro",
    capacidades=("ligar",),
    auth=Auth.CODIGO,
    config_campos=(
        Campo("porta", TipoCampo.INTEIRO, padrao="8080"),
        Campo("senha", TipoCampo.SEGREDO),
    ),
    textos={
        idioma: {
            "descricao": "Exemplo",
            "auth_ajuda": "Ajuda",
            "campo_porta": "Porta",
            "campo_senha": "Senha",
        }
        for idioma in ("pt", "en")
    },
)


class Falso(Driver):
    """A driver that records what reached it, so a test proves what never did.

    Um driver que guarda o que chegou nele, para um teste provar o que nunca chegou.
    """

    MANIFESTO = MANIFESTO
    instancias: list["Falso"] = []

    def __init__(self, cadastro) -> None:
        super().__init__(cadastro)
        self.executados: list[tuple[str, object]] = []
        self.autenticacoes = 0
        self._defina(online=True, ligado=False)
        type(self).instancias.append(self)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        self.executados.append((acao, valor))
        return None

    async def autenticar(self) -> str:
        self.autenticacoes += 1
        return "pareado"


@pytest.fixture
def catalogo() -> dict[str, type[Driver]]:
    Falso.instancias = []
    return {TIPO: Falso}


@pytest.fixture
async def cliente_eq(fabrica_cliente, catalogo):
    return await fabrica_cliente(catalogo=catalogo)


@pytest.fixture
async def com_dono(cliente_eq, posse, bearer):
    return cliente_eq, bearer(await posse(cliente_eq))


def _confere_cabecalhos(resposta) -> None:
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_nenhuma_rota_de_equipamento_responde_sem_sessao(cliente_eq, metodo, caminho):
    resposta = await cliente_eq.request(metodo, caminho, json=CORPO)
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "nao_autenticado"}
    _confere_cabecalhos(resposta)


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_token_inventado_nao_abre_nenhuma_rota(cliente_eq, metodo, caminho, bearer):
    resposta = await cliente_eq.request(
        metodo, caminho, json=CORPO, headers=bearer("nao-sou-token")
    )
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "sessao_invalida"}
    _confere_cabecalhos(resposta)


async def test_cadastro_sem_sessao_nao_acontece_por_tras_do_401(cliente_eq, amb, posse, bearer):
    assert (await cliente_eq.post("/api/equipamentos", json=CORPO)).status == 401
    assert Falso.instancias == []
    auth = bearer(await posse(cliente_eq))
    lista = (await (await cliente_eq.get("/api/equipamentos", headers=auth)).json())["equipamentos"]
    assert lista == []
    em_disco = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    assert em_disco["equipamentos"] == []


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_toda_rota_de_equipamento_carrega_os_cabecalhos(com_dono, metodo, caminho):
    cliente, auth = com_dono
    resposta = await cliente.request(metodo, caminho, json=CORPO, headers=auth)
    _confere_cabecalhos(resposta)
    assert resposta.headers["Content-Type"].startswith("application/json")


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_origin_de_outro_site_e_403_em_toda_rota_de_equipamento(com_dono, metodo, caminho):
    # Why: the session lives in the browser of the integrator, so a page of the attacker
    # would command the hub with it if the Origin were not checked.
    # Por que: a sessão vive no navegador do integrador, então uma página do atacante
    # comandaria o hub com ela se o Origin não fosse conferido.
    cliente, auth = com_dono
    cabecalhos = {**auth, "Origin": "http://evil.example.com"}
    resposta = await cliente.request(metodo, caminho, json=CORPO, headers=cabecalhos)
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "origem_nao_permitida"}
    assert Falso.instancias == []


NOMES_E_URLS = [
    "aparelho.local",
    "localhost",
    "http://192.0.2.10",
    "192.0.2.10:8080",
    "127.0.0.1:8080",
    "[::1]",
    "::1%eth0",
    "fe80::1%25eth0",
    "192.0.2.10/24",
    " 192.0.2.10",
    "192.0.2.10 ",
    "192.0.2.010",
    "0x7f.0.0.1",
    "2130706433",
    "192.0.2.10\x00",
    "192.0.2.10\n192.0.2.11",
    "",
    "a" * 5000,
]


@pytest.mark.parametrize("texto", NOMES_E_URLS)
def test_o_validador_de_ip_recusa_tudo_que_nao_e_endereco(texto):
    assert ip_literal(texto) is None


@pytest.mark.parametrize("texto", [None, 7, True, ["192.0.2.10"], b"192.0.2.10"])
def test_o_validador_de_ip_recusa_o_que_nem_texto_e(texto):
    assert ip_literal(texto) is None


@pytest.mark.parametrize(
    ("texto", "canonico"),
    [("192.0.2.10", "192.0.2.10"), ("::1", "::1"), ("2001:DB8::0:1", "2001:db8::1")],
)
def test_o_validador_de_ip_aceita_endereco_e_devolve_a_forma_canonica(texto, canonico):
    assert ip_literal(texto) == canonico


@pytest.mark.parametrize("texto", NOMES_E_URLS)
async def test_a_rota_de_cadastro_recusa_um_ip_que_nao_e_literal(com_dono, texto):
    # Why: section 9, a name would make the hub resolve and reach whatever the caller wrote,
    # which turns the daemon into a proxy into the LAN of the client.
    # Por que: seção 9, um nome faria o hub resolver e alcançar o que quem chamou escreveu,
    # o que transforma o daemon em proxy para a LAN do cliente.
    cliente, auth = com_dono
    resposta = await cliente.post("/api/equipamentos", json={**CORPO, "ip": texto}, headers=auth)
    assert resposta.status == 400
    assert await resposta.json() == {"ok": False, "code": "ip_invalido"}
    assert Falso.instancias == []


async def test_a_rota_de_atualizacao_tambem_recusa_um_nome_no_ip(com_dono):
    cliente, auth = com_dono
    assert (await cliente.post("/api/equipamentos", json=CORPO, headers=auth)).status == 200
    corpo = {**CORPO, "ip": "aparelho.local"}
    resposta = await cliente.post(f"/api/equipamentos/{IDENTIDADE}", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "ip_invalido"
    (equipamento,) = (await (await cliente.get("/api/equipamentos", headers=auth)).json())[
        "equipamentos"
    ]
    assert equipamento["ip"] == "192.0.2.10"


async def _cadastrar(cliente, auth) -> None:
    resposta = await cliente.post("/api/equipamentos", json=CORPO, headers=auth)
    assert resposta.status == 200, await resposta.text()


async def test_o_segredo_do_aparelho_nunca_volta_em_resposta_nenhuma(com_dono):
    cliente, auth = com_dono
    await _cadastrar(cliente, auth)
    # Why: the removal goes last, so every other route answers about an equipment that is
    # still registered and really holds the credential.
    # Por que: a remoção fica por último, para toda outra rota responder sobre um equipamento
    # ainda cadastrado e que de fato guarda a credencial.
    ordenadas = [rota for rota in ROTAS if rota[0] != "DELETE"]
    ordenadas += [rota for rota in ROTAS if rota[0] == "DELETE"]
    corpos = []
    for metodo, caminho in ordenadas:
        resposta = await cliente.request(metodo, caminho, json=CORPO, headers=auth)
        corpos.append(f"{metodo} {caminho} {resposta.status} {await resposta.text()}")
    ofensores = [texto for texto in corpos if SENHA_DO_APARELHO in texto]
    assert not ofensores, ofensores


async def test_o_segredo_digitado_na_chave_errada_do_config_nao_sai(
    fabrica_cliente, catalogo, posse, bearer
):
    # Why: a config.json edited by hand can carry the password in campos, and the answer of
    # the panel must not hand it back just because it was typed in the wrong key.
    # Por que: um config.json editado à mão pode levar a senha em campos, e a resposta do
    # painel não pode devolvê-la só porque ela foi digitada na chave errada.
    guardado = Cadastro(
        identidade=IDENTIDADE,
        tipo=TIPO,
        ip="192.0.2.10",
        campos={"porta": "8080", "senha": SENHA_DO_APARELHO},
    )
    cliente = await fabrica_cliente(catalogo=catalogo, config=Config(equipamentos=(guardado,)))
    auth = bearer(await posse(cliente))
    resposta = await cliente.get("/api/equipamentos", headers=auth)
    texto = await resposta.text()
    assert SENHA_DO_APARELHO not in texto, texto
    (equipamento,) = (await resposta.json())["equipamentos"]
    assert equipamento["campos"] == {"porta": "8080"}


async def test_o_segredo_de_um_tipo_que_saiu_da_imagem_nao_sai_por_rota_nenhuma(
    fabrica_cliente, catalogo, posse, bearer
):
    # Why: with no manifest nothing says which key of the registration is a credential, and
    # this is exactly the case the filter exists for: a tipo that left the image, or a
    # config.json edited by hand. A filter that cannot tell has to answer nothing.
    # Por que: sem manifesto nada diz qual chave do cadastro é credencial, e este é justamente
    # o caso para o qual o filtro existe: um tipo que saiu da imagem, ou um config.json editado
    # à mão. Um filtro que não sabe tem de responder nada.
    guardado = Cadastro(
        identidade=IDENTIDADE,
        tipo="sumiu",
        ip="192.0.2.10",
        campos={"porta": "8080", "senha": SENHA_DO_APARELHO},
        segredos={"senha": SENHA_DO_APARELHO},
    )
    cliente = await fabrica_cliente(catalogo=catalogo, config=Config(equipamentos=(guardado,)))
    auth = bearer(await posse(cliente))
    (equipamento,) = (await (await cliente.get("/api/equipamentos", headers=auth)).json())[
        "equipamentos"
    ]
    assert equipamento["campos"] == {}
    assert equipamento["segredos_definidos"] == []
    ordenadas = [rota for rota in ROTAS if rota[0] != "DELETE"]
    ordenadas += [rota for rota in ROTAS if rota[0] == "DELETE"]
    corpos = []
    for metodo, caminho in ordenadas:
        resposta = await cliente.request(metodo, caminho, json=CORPO, headers=auth)
        corpos.append(f"{metodo} {caminho} {resposta.status} {await resposta.text()}")
    ofensores = [texto for texto in corpos if SENHA_DO_APARELHO in texto]
    assert not ofensores, ofensores


async def test_o_catalogo_nao_carrega_valor_de_segredo_nenhum(com_dono):
    cliente, auth = com_dono
    await _cadastrar(cliente, auth)
    texto = await (await cliente.get("/api/catalogo", headers=auth)).text()
    assert SENHA_DO_APARELHO not in texto
    (item,) = json.loads(texto)["catalogo"]
    assert [campo["nome"] for campo in item["config_campos"]] == ["porta", "senha"]


async def test_acao_fora_das_capacidades_nao_chega_ao_driver(com_dono):
    # Why: section 6, the gate refuses before the driver is touched, so no driver ever writes
    # a method only to say no, and nothing reaches the socket of the device.
    # Por que: seção 6, o portão recusa antes de tocar no driver, então nenhum driver escreve
    # método só para dizer não, e nada chega ao socket do aparelho.
    cliente, auth = com_dono
    await _cadastrar(cliente, auth)
    driver = Falso.instancias[0]
    for acao in ("volume", "agrupar", "nao_existe", "desligar"):
        resposta = await cliente.post(
            f"/api/equipamentos/{IDENTIDADE}/acao", json={"acao": acao}, headers=auth
        )
        assert resposta.status == 400, acao
        assert await resposta.json() == {"ok": False, "code": "nao_suportado"}, acao
    assert driver.executados == []
    ligada = await cliente.post(
        f"/api/equipamentos/{IDENTIDADE}/acao", json={"acao": "ligar"}, headers=auth
    )
    assert ligada.status == 200
    assert driver.executados == [("ligar", None)]


async def test_identidade_no_caminho_nao_alcanca_outra_rota(com_dono):
    # Why: the identity is a uuid, a MAC or a serial written by whoever holds a session, and
    # an encoded slash inside one must stay a name of equipment, never a second path segment.
    # Por que: a identidade é uuid, MAC ou serial escrita por quem tem sessão, e uma barra
    # codificada dentro dela precisa seguir sendo nome de equipamento, nunca outro segmento.
    cliente, auth = com_dono
    for bruto in ("..%2Festado", "%2E%2E%2F%2E%2E%2Fapi%2Festado", "uuid-1%00", "uuid%2F1"):
        resposta = await cliente.post(
            f"/api/equipamentos/{bruto}/acao", json={"acao": "ligar"}, headers=auth
        )
        assert resposta.status == 404, bruto
        assert await resposta.json() == {"ok": False, "code": "eq_nao_encontrado"}, bruto
    assert Falso.instancias == []


async def test_corpo_gigante_nao_e_lido_nem_guardado(com_dono):
    cliente, auth = com_dono
    enorme = {**CORPO, "nome": "a" * (CORPO_MAXIMO + 1024)}
    resposta = await cliente.post("/api/equipamentos", json=enorme, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "corpo_invalido"
    assert Falso.instancias == []
