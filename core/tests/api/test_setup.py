# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the setup routes: shape, stable codes and what each one changes.

O contrato das rotas de setup: forma, códigos estáveis e o que cada uma muda.
"""

import asyncio
import json
import threading

import pytest

from iphub.api import setup
from iphub.api.comum import CORPO_MAXIMO
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.limite import Limite
from iphub.sessoes import VALIDADE_S
from iphub.versao import SCHEMA_VERSION, VERSAO


def _quase_a_validade(segundos: int) -> bool:
    # Why: the store counts whole seconds from a real clock, so the last one is truncated.
    # Por que: o repositório conta segundos inteiros de um relógio real, então o último
    # é truncado.
    return VALIDADE_S - 5 <= segundos <= VALIDADE_S


OUTRA_SENHA = "outra-senha-boa"
COM_SESSAO = (("POST", "/api/sair"), ("GET", "/api/sessao"), ("POST", "/api/senha"))


class LimiteEspiao(Limite):
    """A limiter that counts how often a route declared an attempt worth a PBKDF2.

    Um limitador que conta quantas vezes uma rota declarou tentativa que vale um PBKDF2.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tentativas = 0

    def registrar_tentativa(self) -> None:
        self.tentativas += 1
        super().registrar_tentativa()


async def _falar_no_socket(cliente, pedido: bytes, resto: bytes = b"", pausa: float = 0.0) -> bytes:
    """Speaks HTTP over a raw socket, which is the only way to split or corrupt the bytes.

    Fala HTTP num socket cru, o único jeito de partir ou corromper os bytes.
    """
    leitor, escritor = await asyncio.open_connection(cliente.host, cliente.port)
    escritor.write(pedido)
    await escritor.drain()
    if resto:
        await asyncio.sleep(pausa)
        escritor.write(resto)
        await escritor.drain()
    resposta = await leitor.read()
    escritor.close()
    await escritor.wait_closed()
    return resposta


async def test_estado_e_publico_e_diz_que_nao_ha_dono(cliente):
    resposta = await cliente.get("/api/estado")
    assert resposta.status == 200
    assert resposta.content_type == "application/json"
    assert await resposta.json() == {
        "ok": True,
        "code": None,
        "configurado": False,
        "versao": VERSAO,
        "schema_version": SCHEMA_VERSION,
        "nome_instalacao": "",
    }


async def test_posse_define_a_senha_e_abre_a_sessao(cliente, codigo, senha):
    resposta = await cliente.post("/api/posse", json={"codigo": codigo, "senha": senha})
    assert resposta.status == 200
    corpo = await resposta.json()
    assert corpo["ok"] is True
    assert corpo["code"] is None
    assert isinstance(corpo["token"], str) and len(corpo["token"]) >= 32
    assert _quase_a_validade(corpo["expira_em_s"])
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is True


async def test_sessao_devolve_o_que_falta_da_validade(cliente, posse, bearer):
    token = await posse(cliente)
    resposta = await cliente.get("/api/sessao", headers=bearer(token))
    assert resposta.status == 200
    corpo = await resposta.json()
    assert corpo["ok"] is True and corpo["code"] is None
    assert _quase_a_validade(corpo["expira_em_s"])


async def test_entrar_com_a_senha_certa_abre_outra_sessao(cliente, posse, senha, bearer):
    primeiro = await posse(cliente)
    resposta = await cliente.post("/api/entrar", json={"senha": senha})
    assert resposta.status == 200
    segundo = (await resposta.json())["token"]
    assert segundo != primeiro
    for token in (primeiro, segundo):
        assert (await cliente.get("/api/sessao", headers=bearer(token))).status == 200


async def test_entrar_antes_de_haver_dono_e_409(cliente, senha):
    resposta = await cliente.post("/api/entrar", json={"senha": senha})
    assert resposta.status == 409
    assert await resposta.json() == {"ok": False, "code": "nao_configurado"}


async def test_sair_encerra_a_sessao(cliente, posse, bearer):
    token = await posse(cliente)
    resposta = await cliente.post("/api/sair", headers=bearer(token))
    assert resposta.status == 200
    assert await resposta.json() == {"ok": True, "code": None}
    assert (await cliente.get("/api/sessao", headers=bearer(token))).status == 401


async def test_trocar_a_senha_abre_sessao_nova_e_mata_a_senha_velha(cliente, posse, senha, bearer):
    token = await posse(cliente)
    resposta = await cliente.post(
        "/api/senha",
        headers=bearer(token),
        json={"senha_atual": senha, "senha_nova": OUTRA_SENHA},
    )
    assert resposta.status == 200
    corpo = await resposta.json()
    assert _quase_a_validade(corpo["expira_em_s"])
    assert (await cliente.get("/api/sessao", headers=bearer(corpo["token"]))).status == 200
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 401
    assert (await cliente.post("/api/entrar", json={"senha": OUTRA_SENHA})).status == 200


@pytest.mark.parametrize("corpo", [None, "nao e json", "[]", '"texto"', "{}", '{"codigo": 1}'])
@pytest.mark.parametrize("caminho", ["/api/posse", "/api/entrar"])
async def test_corpo_que_nao_e_o_objeto_esperado_e_400(cliente, caminho, corpo):
    resposta = await cliente.post(caminho, data=corpo)
    assert resposta.status == 400
    assert await resposta.json() == {"ok": False, "code": "corpo_invalido"}


async def test_corpo_grande_demais_e_400(cliente, codigo):
    # Why: the caller is not authenticated yet, so a body it can make arbitrarily large is
    # memory an attacker spends for free.
    # Por que: quem chama ainda não está autenticado, então um corpo que ele faz do tamanho
    # que quiser é memória que um atacante gasta de graça.
    enchimento = "a" * CORPO_MAXIMO
    resposta = await cliente.post(
        "/api/posse", json={"codigo": codigo, "senha": "x" * 9, "enchimento": enchimento}
    )
    assert resposta.status == 400
    assert await resposta.json() == {"ok": False, "code": "corpo_invalido"}
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is False


@pytest.mark.parametrize(("metodo", "caminho"), COM_SESSAO)
async def test_rota_de_sessao_sem_cabecalho_e_401(cliente, metodo, caminho):
    resposta = await cliente.request(metodo, caminho)
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "nao_autenticado"}


@pytest.mark.parametrize("valor", ["", "Basic abc", "Token abc", "Bearer", "Bearer    "])
async def test_cabecalho_que_nao_e_bearer_e_nao_autenticado(cliente, valor):
    resposta = await cliente.get("/api/sessao", headers={"Authorization": valor})
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "nao_autenticado"}


async def test_bearer_com_token_desconhecido_e_sessao_invalida(cliente, bearer):
    resposta = await cliente.get("/api/sessao", headers=bearer("token-que-ninguem-emitiu"))
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "sessao_invalida"}


@pytest.mark.parametrize(
    ("metodo", "caminho", "permitido"),
    [
        ("POST", "/api/estado", "GET"),
        ("GET", "/api/posse", "POST"),
        ("GET", "/api/entrar", "POST"),
        ("GET", "/api/sair", "POST"),
        ("POST", "/api/sessao", "GET"),
        ("DELETE", "/api/senha", "POST"),
    ],
)
async def test_metodo_errado_nas_rotas_novas_e_405(cliente, metodo, caminho, permitido):
    resposta = await cliente.request(metodo, caminho)
    assert resposta.status == 405
    assert await resposta.json() == {"ok": False, "code": "metodo_nao_permitido"}
    assert permitido in resposta.headers["Allow"]


async def test_corpo_que_chega_em_dois_segmentos_ainda_e_lido(cliente, codigo, senha):
    # Why: a single read returns only what the buffer already holds, so an honest client whose
    # body crossed two TCP segments was answered corpo_invalido and could not log in.
    # Por que: uma leitura só devolve o que o buffer já tem, então um cliente honesto cujo
    # corpo cruzou dois segmentos TCP recebia corpo_invalido e não conseguia entrar.
    corpo = json.dumps({"codigo": codigo, "senha": senha}).encode("utf-8")
    cabecalho = (
        b"POST /api/posse HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(corpo)
    )
    corte = len(corpo) // 2
    resposta = await _falar_no_socket(cliente, cabecalho + corpo[:corte], corpo[corte:], 0.2)
    assert resposta.startswith(b"HTTP/1.1 200 OK"), resposta[:200]
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is True


async def test_trocar_a_senha_preserva_o_que_foi_editado_no_arquivo(
    cliente, posse, senha, bearer, amb
):
    token = await posse(cliente)
    caminho = amb.dir_data / ARQUIVO_CONFIG
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["hosts_permitidos"] = ["hub.local"]
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    resposta = await cliente.post(
        "/api/senha", headers=bearer(token), json={"senha_atual": senha, "senha_nova": OUTRA_SENHA}
    )
    assert resposta.status == 200
    # Why: the integrator edits this file by hand between two actions of the panel, and a route
    # that wrote its boot time snapshot back would undo the edit without saying so.
    # Por que: o integrador edita este arquivo na mão entre duas ações do painel, e uma rota que
    # regravasse o retrato do boot desfaria a edição sem avisar.
    assert json.loads(caminho.read_text(encoding="utf-8"))["hosts_permitidos"] == ["hub.local"]
    assert (await cliente.get("/api/estado", headers={"Host": "hub.local"})).status == 200


async def test_corpo_invalido_e_estado_errado_nao_gastam_a_janela_global(
    fabrica_cliente, codigo, senha
):
    espiao = LimiteEspiao()
    cliente = await fabrica_cliente(limite=espiao)
    assert (await cliente.post("/api/entrar", data="nao e json")).status == 400
    assert (await cliente.post("/api/posse", data="nao e json")).status == 400
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 409
    # Why: section 9 buys the window of sixty a minute with the cost of one PBKDF2, so a request
    # that checks no secret must not spend it; otherwise anyone on the LAN locks the owner out.
    # Por que: a seção 9 compra a janela de sessenta por minuto com o custo de um PBKDF2, então
    # requisição que não confere segredo não pode gastá-la; senão qualquer um na LAN tranca o
    # dono para fora.
    assert espiao.tentativas == 0
    assert (await cliente.post("/api/posse", json={"codigo": codigo, "senha": senha})).status == 200
    assert (await cliente.post("/api/posse", json={"codigo": codigo, "senha": senha})).status == 409
    assert espiao.tentativas == 1


async def test_trocar_a_senha_paga_na_janela_global(fabrica_cliente, posse, senha, bearer):
    espiao = LimiteEspiao()
    cliente = await fabrica_cliente(limite=espiao)
    token = await posse(cliente)
    assert espiao.tentativas == 1
    resposta = await cliente.post(
        "/api/senha", headers=bearer(token), json={"senha_atual": senha, "senha_nova": OUTRA_SENHA}
    )
    assert resposta.status == 200
    # Why: this route spends two PBKDF2 per call, exactly the cost the window exists to bound.
    # Por que: esta rota gasta dois PBKDF2 por chamada, exatamente o custo que a janela limita.
    assert espiao.tentativas == 2


async def test_o_pbkdf2_nao_roda_no_laco_de_eventos(cliente, codigo, senha, monkeypatch):
    fios = []

    def espiar(funcao):
        def espiao(*argumentos):
            fios.append(threading.current_thread())
            return funcao(*argumentos)

        return espiao

    monkeypatch.setattr(setup, "gerar_hash", espiar(setup.gerar_hash))
    monkeypatch.setattr(setup, "conferir_senha", espiar(setup.conferir_senha))
    assert (await cliente.post("/api/posse", json={"codigo": codigo, "senha": senha})).status == 200
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 200
    # Why: two hundred thousand iterations freeze every other request of the panel while they
    # run on the reference ARM board, so the derivation belongs on a thread of its own.
    # Por que: duzentas mil iterações congelam toda outra requisição do painel enquanto rodam na
    # placa ARM de referência, então a derivação fica num fio próprio.
    assert len(fios) == 2
    assert threading.main_thread() not in fios
