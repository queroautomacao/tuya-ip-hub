# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: only whoever holds the ownership code becomes the owner, plus the password rules.

Seção 9: só quem tem o código de posse vira dono, mais as regras da senha.
"""

import json
from pathlib import Path

import pytest

from iphub.api.comum import segredos_de
from iphub.arquivos import modo_de
from iphub.auth import ITERACOES, SENHA_MINIMA, TAMANHO_SALT
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.segredos import ARQUIVO_CODIGO, ARQUIVO_TOKEN
from iphub.sessoes import Sessoes

CURTA = "1234567"
OUTRA_SENHA = "outra-senha-boa"


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8").strip()


def _outro_codigo(codigo: str) -> str:
    """The same shape, one character off, so the test never guesses the real code.

    A mesma forma, um caractere trocado, para o teste nunca acertar o código real.
    """
    return ("A" if codigo[0] != "A" else "B") + codigo[1:]


@pytest.mark.parametrize("tentado", ["", "errado", "AAAA-AAAA-AAAA-AAAA", "-"])
async def test_sem_o_codigo_ninguem_vira_dono(cliente, senha, tentado):
    resposta = await cliente.post("/api/posse", json={"codigo": tentado, "senha": senha})
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "codigo_invalido"}
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is False
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 409


async def test_codigo_com_um_caractere_trocado_e_recusado(cliente, codigo, senha):
    resposta = await cliente.post(
        "/api/posse", json={"codigo": _outro_codigo(codigo), "senha": senha}
    )
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "codigo_invalido"}


async def test_codigo_ditado_em_voz_alta_e_aceito(cliente, codigo, senha):
    # Why: the integrator reads the code from the log and types it, so case, blanks and
    # hyphens cannot be the difference between owning the hub and not owning it.
    # Por que: o integrador lê o código no log e digita, então caixa, brancos e hifens não
    # podem ser a diferença entre ser dono do hub e não ser.
    informado = codigo.replace("-", " ").lower()
    resposta = await cliente.post("/api/posse", json={"codigo": informado, "senha": senha})
    assert resposta.status == 200
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is True


async def test_hub_com_dono_nao_diz_nada_sobre_o_codigo(cliente, codigo, posse, senha):
    await posse(cliente)
    certo = await cliente.post("/api/posse", json={"codigo": codigo, "senha": OUTRA_SENHA})
    errado = await cliente.post(
        "/api/posse", json={"codigo": _outro_codigo(codigo), "senha": OUTRA_SENHA}
    )
    # Why: a hub that answered differently to the right code would be an oracle for a brute
    # force run against the code of every hub of this model.
    # Por que: um hub que respondesse diferente ao código certo seria um oráculo para uma
    # varredura de força bruta contra o código de todo hub deste modelo.
    assert certo.status == 409
    assert errado.status == 409
    assert await certo.json() == {"ok": False, "code": "ja_configurado"}
    assert await errado.json() == await certo.json()
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 200


async def test_senha_curta_e_recusada_na_posse(cliente, codigo):
    assert len(CURTA) == SENHA_MINIMA - 1
    resposta = await cliente.post("/api/posse", json={"codigo": codigo, "senha": CURTA})
    assert resposta.status == 400
    assert await resposta.json() == {"ok": False, "code": "senha_curta"}
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is False


async def test_senha_curta_e_recusada_na_troca(cliente, posse, senha, bearer):
    token = await posse(cliente)
    resposta = await cliente.post(
        "/api/senha", headers=bearer(token), json={"senha_atual": senha, "senha_nova": CURTA}
    )
    assert resposta.status == 400
    assert await resposta.json() == {"ok": False, "code": "senha_curta"}
    assert (await cliente.get("/api/sessao", headers=bearer(token))).status == 200
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 200


async def test_trocar_a_senha_exige_a_senha_atual(cliente, posse, senha, bearer):
    token = await posse(cliente)
    resposta = await cliente.post(
        "/api/senha",
        headers=bearer(token),
        json={"senha_atual": "nao-e-a-senha", "senha_nova": OUTRA_SENHA},
    )
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "senha_invalida"}
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 200
    assert (await cliente.post("/api/entrar", json={"senha": OUTRA_SENHA})).status == 401


@pytest.mark.parametrize("tentada", ["", "nao-e-a-senha", "SENHA-DE-TESTE"])
async def test_senha_errada_nao_entra(cliente, posse, tentada):
    await posse(cliente)
    resposta = await cliente.post("/api/entrar", json={"senha": tentada})
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "senha_invalida"}


async def test_o_hash_guardado_tem_as_duzentas_mil_iteracoes(cliente, posse, senha, amb):
    await posse(cliente)
    bruto = (amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8")
    dados = json.loads(bruto)
    assert ITERACOES == 200_000
    assert dados["senha_iteracoes"] == ITERACOES
    assert len(bytes.fromhex(dados["senha_salt"])) == TAMANHO_SALT
    assert len(bytes.fromhex(dados["senha_hash"])) == 32
    assert senha not in bruto


async def test_cada_instalacao_tem_o_seu_salt(cliente, posse, senha, amb, bearer):
    token = await posse(cliente)
    primeiro = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    await cliente.post(
        "/api/senha", headers=bearer(token), json={"senha_atual": senha, "senha_nova": senha}
    )
    segundo = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    # Why: the same password written twice must not produce the same hash, or one rainbow
    # table would open every hub that chose it.
    # Por que: a mesma senha escrita duas vezes não pode dar o mesmo hash, senão uma tabela
    # arco-íris abriria todo hub que a escolheu.
    assert segundo["senha_salt"] != primeiro["senha_salt"]
    assert segundo["senha_hash"] != primeiro["senha_hash"]


async def test_a_posse_derruba_a_sessao_de_quem_era_dono(
    fabrica_cliente, amb, codigo, senha, bearer
):
    amb.dir_data.mkdir(parents=True, exist_ok=True)
    antigo, _ = Sessoes(amb.dir_data).criar()
    cliente = await fabrica_cliente()
    assert (await cliente.get("/api/sessao", headers=bearer(antigo))).status == 200
    # Why: a data directory whose config.json was erased by hand is an unconfigured hub again,
    # and whoever takes it over must not inherit the session the previous owner left open.
    # Por que: um diretório de dados com o config.json apagado na mão é de novo um hub sem dono,
    # e quem o toma não pode herdar a sessão que o dono anterior deixou aberta.
    assert (await cliente.post("/api/posse", json={"codigo": codigo, "senha": senha})).status == 200
    resposta = await cliente.get("/api/sessao", headers=bearer(antigo))
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "sessao_invalida"}


async def test_a_posse_gasta_o_codigo_e_a_credencial_de_maquina(cliente, codigo, senha, amb):
    token_antes = _ler(amb.dir_data / ARQUIVO_TOKEN)
    assert (await cliente.post("/api/posse", json={"codigo": codigo, "senha": senha})).status == 200
    # Why: the code is printed in the container log at first boot and never expires by itself,
    # so an old log line would take a wiped hub a second time; the machine credential the
    # previous owner holds has to fall with it.
    # Por que: o código é impresso no log do container no primeiro boot e não vence sozinho,
    # então uma linha velha de log tomaria um hub apagado uma segunda vez; a credencial de
    # máquina que o dono anterior tem precisa cair junto.
    novo_codigo = _ler(amb.dir_data / ARQUIVO_CODIGO)
    novo_token = _ler(amb.dir_data / ARQUIVO_TOKEN)
    assert novo_codigo != codigo
    assert novo_token != token_antes
    assert modo_de(amb.dir_data / ARQUIVO_CODIGO) == 0o600
    assert modo_de(amb.dir_data / ARQUIVO_TOKEN) == 0o600
    vivos = segredos_de(cliente.server.app)
    assert vivos.codigo_de_posse == novo_codigo
    assert vivos.api_token == novo_token
