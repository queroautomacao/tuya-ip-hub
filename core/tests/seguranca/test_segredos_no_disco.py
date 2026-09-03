# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: every file with a secret is 0600, and no secret ever leaves in a response.

Seção 9: todo arquivo com segredo é 0600, e nenhum segredo sai numa resposta.
"""

import os
from pathlib import Path

from iphub.api.comum import segredos_de
from iphub.arquivos import modo_de
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.segredos import ARQUIVO_TOKEN
from iphub.segredos import abrir as abrir_segredos
from iphub.sessoes import ARQUIVO as ARQUIVO_SESSOES
from iphub.sessoes import impressao

OUTRA_SENHA = "outra-senha-boa"
COM_SEGREDO = (ARQUIVO_CONFIG, ARQUIVO_TOKEN, ARQUIVO_SESSOES)


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8").strip()


def _tudo_que_a_resposta_diz(resposta, texto: str) -> str:
    return texto + "\n" + "\n".join(f"{nome}: {valor}" for nome, valor in resposta.headers.items())


async def _fluxo_completo(cliente, posse, senha, bearer) -> str:
    token = await posse(cliente)
    await cliente.get("/api/sessao", headers=bearer(token))
    await cliente.post("/api/entrar", json={"senha": senha})
    resposta = await cliente.post(
        "/api/senha", headers=bearer(token), json={"senha_atual": senha, "senha_nova": OUTRA_SENHA}
    )
    assert resposta.status == 200
    return (await resposta.json())["token"]


async def test_todo_arquivo_com_segredo_nasce_0600(cliente, posse, senha, bearer, amb):
    await _fluxo_completo(cliente, posse, senha, bearer)
    for nome in COM_SEGREDO:
        caminho = amb.dir_data / nome
        assert caminho.is_file(), nome
        assert modo_de(caminho) == 0o600, nome


async def test_nenhuma_rota_devolve_o_api_token(cliente, senha, bearer, amb):
    arquivo_token = amb.dir_data / ARQUIVO_TOKEN
    guardados = {_ler(arquivo_token)}
    pedidos = (
        ("GET", "/api/estado", {}),
        ("POST", "/api/posse", {"json": {"senha": "curta"}}),
        ("POST", "/api/entrar", {"json": {"senha": senha}}),
        ("POST", "/api/posse", {"json": {"senha": senha}}),
        ("POST", "/api/entrar", {"json": {"senha": "nao-e-a-senha"}}),
        ("GET", "/api/sessao", {"headers": bearer("token-que-ninguem-emitiu")}),
        ("GET", "/api/estado", {"headers": {"Host": "evil.example.com"}}),
        ("GET", "/api/estado", {"headers": {"Origin": "http://evil.example.com"}}),
        ("POST", "/api/estado", {}),
        ("GET", "/health", {}),
        ("GET", "/", {}),
        ("GET", "/nao-existe", {}),
    )
    for metodo, caminho, extras in pedidos:
        resposta = await cliente.request(metodo, caminho, **extras)
        guardados.add(_ler(arquivo_token))
        dito = _tudo_que_a_resposta_diz(resposta, await resposta.text())
        for segredo in guardados:
            assert segredo not in dito, f"{metodo} {caminho}"


async def test_trocar_a_senha_rotaciona_o_api_token(cliente, posse, senha, bearer, amb):
    arquivo_token = amb.dir_data / ARQUIVO_TOKEN
    antes = _ler(arquivo_token)
    novo_da_sessao = await _fluxo_completo(cliente, posse, senha, bearer)
    depois = _ler(arquivo_token)
    # Why: the machine credential is what the DP-bus authenticates with, so a password change
    # made because of a leak has to take it down too.
    # Por que: a credencial de máquina é com o que o DP-bus autentica, então uma troca de
    # senha feita por causa de um vazamento precisa derrubá-la também.
    assert depois != antes
    assert len(depois) >= 32
    assert modo_de(arquivo_token) == 0o600
    assert segredos_de(cliente.server.app).api_token == depois
    assert novo_da_sessao != depois


async def test_o_token_de_sessao_nao_e_gravado_em_claro(cliente, posse, amb):
    token = await posse(cliente)
    conteudo = (amb.dir_data / ARQUIVO_SESSOES).read_text(encoding="utf-8")
    # Why: whoever reads the file must not come out of it with a session; a fingerprint is
    # enough for the daemon to recognize the token it was given.
    # Por que: quem lê o arquivo não pode sair dele com uma sessão; a impressão basta para o
    # daemon reconhecer o token que recebeu.
    assert token not in conteudo
    assert impressao(token) in conteudo


async def test_a_senha_nao_e_gravada_em_claro(cliente, posse, senha, amb):
    await posse(cliente)
    conteudo = (amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8")
    assert senha not in conteudo


async def test_o_diretorio_de_dados_nao_se_abre_para_outro_usuario(fabrica_cliente, amb, senha):
    # Why: this directory holds the four files of section 9; created with the umask of the
    # host, 022 in practice, any local user lists the names of the secrets in it.
    # Por que: este diretório guarda os quatro arquivos da seção 9; criado com o umask do
    # hospedeiro, 022 na prática, qualquer usuário local lista os nomes dos segredos nele.
    anterior = os.umask(0o000)
    try:
        cliente = await fabrica_cliente()
    finally:
        os.umask(anterior)
    resposta = await cliente.post("/api/posse", json={"senha": senha})
    assert resposta.status == 200, await resposta.text()
    assert modo_de(amb.dir_data) == 0o700


def test_link_no_lugar_do_token_nao_entrega_arquivo_de_fora(amb):
    # Why: a link planted at api-token.txt would make the daemon read any file it can open and
    # take the content as the machine credential the DP-bus accepts.
    # Por que: um link plantado em api-token.txt faria o daemon ler qualquer arquivo que ele
    # consiga abrir e tomar o conteúdo como a credencial de máquina que o DP-bus aceita.
    amb.dir_data.mkdir(parents=True, exist_ok=True)
    fora = amb.dir_data.parent / "segredo-do-hospedeiro.txt"
    fora.write_text("SEGREDO-DO-HOSPEDEIRO\n", encoding="utf-8")
    (amb.dir_data / ARQUIVO_TOKEN).symlink_to(fora)
    segredo = abrir_segredos(amb.dir_data)
    assert "SEGREDO-DO-HOSPEDEIRO" not in segredo.api_token
    assert not (amb.dir_data / ARQUIVO_TOKEN).is_symlink()
    assert modo_de(amb.dir_data / ARQUIVO_TOKEN) == 0o600
    # Why: refusing to read through the link must not turn into writing through it either.
    # Por que: recusar a leitura através do link não pode virar escrita através dele.
    assert fora.read_text(encoding="utf-8") == "SEGREDO-DO-HOSPEDEIRO\n"
