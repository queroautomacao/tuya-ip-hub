# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: the first password sets the owner, once and only once, plus the password rules.

Seção 9: a primeira senha define o dono, uma vez e só uma, mais as regras da senha.
"""

import asyncio
import json
from pathlib import Path

import pytest

from iphub.api.comum import segredos_de
from iphub.arquivos import modo_de
from iphub.auth import ITERACOES, SENHA_MINIMA, TAMANHO_SALT
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.segredos import ARQUIVO_TOKEN
from iphub.sessoes import Sessoes

CURTA = "1234567"
OUTRA_SENHA = "outra-senha-boa"


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8").strip()


async def test_a_posse_e_publica_e_so_pede_a_senha(cliente, senha):
    """Section 9: with no ownership code, the first password sets the owner.

    Seção 9: sem código de posse, a primeira senha define o dono.
    """
    resposta = await cliente.post("/api/posse", json={"senha": senha})
    assert resposta.status == 200
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is True
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 200


async def test_hub_com_dono_recusa_uma_segunda_posse(cliente, posse, senha):
    await posse(cliente)
    # Why: ja_configurado is the only guard left on this route, so a hub that already has a
    # password must never let a second claim through, whatever it sends.
    # Por que: o ja_configurado é a única guarda que resta nesta rota, então um hub que já tem
    # senha nunca pode deixar passar uma segunda posse, mande ela o que mandar.
    segunda = await cliente.post("/api/posse", json={"senha": OUTRA_SENHA})
    assert segunda.status == 409
    assert await segunda.json() == {"ok": False, "code": "ja_configurado"}
    assert (await cliente.post("/api/entrar", json={"senha": OUTRA_SENHA})).status == 401
    assert (await cliente.post("/api/entrar", json={"senha": senha})).status == 200


async def test_duas_posses_ao_mesmo_tempo_nao_fazem_dois_donos(cliente):
    """Section 9: the check and the write are one step, so a race never produces two owners.

    Seção 9: a checagem e a escrita são um passo só, então uma corrida nunca faz dois donos.
    """
    senhas = [f"senha-do-concorrente-{i}" for i in range(12)]
    respostas = await asyncio.gather(
        *(cliente.post("/api/posse", json={"senha": s}) for s in senhas)
    )
    corpos = [(r.status, await r.json()) for r in respostas]
    vencedoras = [i for i, (status, _) in enumerate(corpos) if status == 200]
    assert len(vencedoras) == 1, corpos
    for indice, (status, corpo) in enumerate(corpos):
        if indice == vencedoras[0]:
            continue
        assert status == 409
        assert corpo == {"ok": False, "code": "ja_configurado"}
    # Why: only the password of the claim that won may open the panel. One loser is enough to
    # prove it, because a sixth wrong password would trip the per address block of section 9
    # and answer 429 instead of 401, testing the limiter rather than the race.
    # Por que: só a senha da posse que venceu pode abrir o painel. Uma perdedora basta para
    # provar, porque uma sexta senha errada bateria no bloqueio por endereço da seção 9 e
    # responderia 429 em vez de 401, testando o limitador e não a corrida.
    assert (await cliente.post("/api/entrar", json={"senha": senhas[vencedoras[0]]})).status == 200
    perdedora = senhas[(vencedoras[0] + 1) % len(senhas)]
    assert (await cliente.post("/api/entrar", json={"senha": perdedora})).status == 401


async def test_senha_curta_e_recusada_na_posse(cliente):
    assert len(CURTA) == SENHA_MINIMA - 1
    resposta = await cliente.post("/api/posse", json={"senha": CURTA})
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


async def test_a_posse_derruba_a_sessao_de_quem_era_dono(fabrica_cliente, amb, senha, bearer):
    amb.dir_data.mkdir(parents=True, exist_ok=True)
    antigo, _ = Sessoes(amb.dir_data).criar()
    cliente = await fabrica_cliente()
    assert (await cliente.get("/api/sessao", headers=bearer(antigo))).status == 200
    # Why: a data directory whose config.json was erased by hand is an unconfigured hub again,
    # and whoever takes it over must not inherit the session the previous owner left open.
    # Por que: um diretório de dados com o config.json apagado na mão é de novo um hub sem dono,
    # e quem o toma não pode herdar a sessão que o dono anterior deixou aberta.
    assert (await cliente.post("/api/posse", json={"senha": senha})).status == 200
    resposta = await cliente.get("/api/sessao", headers=bearer(antigo))
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "sessao_invalida"}


async def test_a_posse_gasta_a_credencial_de_maquina(cliente, senha, amb):
    token_antes = _ler(amb.dir_data / ARQUIVO_TOKEN)
    assert (await cliente.post("/api/posse", json={"senha": senha})).status == 200
    # Why: a data directory whose config.json was erased by hand is an unconfigured hub again,
    # and the machine credential the previous owner holds has to fall with the ownership.
    # Por que: um diretório de dados com o config.json apagado na mão é de novo um hub sem
    # dono, e a credencial de máquina do dono anterior precisa cair junto com a posse.
    novo_token = _ler(amb.dir_data / ARQUIVO_TOKEN)
    assert novo_token != token_antes
    assert modo_de(amb.dir_data / ARQUIVO_TOKEN) == 0o600
    assert segredos_de(cliente.server.app).api_token == novo_token


async def test_a_posse_nao_cria_arquivo_de_codigo(cliente, senha, posse, amb):
    await posse(cliente)
    assert (
        await cliente.post("/api/senha", json={"senha_atual": senha, "senha_nova": OUTRA_SENHA})
    ).status in (
        200,
        401,
    )
    # Why: the ownership code left section 9; a full flow that still wrote its file would mean
    # dead code keeping a secret on disk that nothing reads.
    # Por que: o código de posse saiu da seção 9; um fluxo completo que ainda escrevesse o
    # arquivo dele significaria código morto guardando em disco um segredo que ninguém lê.
    assert not (amb.dir_data / "codigo-de-posse.txt").exists()
