# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
from dataclasses import replace

from iphub.app import criar_app


async def test_raiz_serve_o_index(cliente, amb):
    resposta = await cliente.get("/")
    assert resposta.status == 200
    assert resposta.content_type == "text/html"
    assert await resposta.text() == (amb.dir_painel / "index.html").read_text(encoding="utf-8")
    assert resposta.headers.get("Cache-Control") == "no-cache"


async def test_assets_servem_arquivo_estatico(cliente, amb):
    resposta = await cliente.get("/assets/app.js")
    assert resposta.status == 200
    assert "javascript" in resposta.content_type
    esperado = (amb.dir_painel / "assets" / "app.js").read_text(encoding="utf-8")
    assert await resposta.text() == esperado


async def test_assets_nao_listam_diretorio(cliente):
    resposta = await cliente.get("/assets/")
    assert resposta.status in {403, 404}
    assert (await resposta.json())["ok"] is False


async def test_assets_nao_saem_da_pasta(cliente):
    resposta = await cliente.get("/assets/..%2Findex.html")
    assert resposta.status in {403, 404}
    assert (await resposta.json())["ok"] is False


async def test_sem_painel_a_raiz_responde_503_json(aiohttp_client, amb, tmp_path):
    sem_painel = replace(amb, dir_painel=tmp_path / "nao-existe")
    cliente = await aiohttp_client(criar_app(sem_painel))
    resposta = await cliente.get("/")
    assert resposta.status == 503
    assert await resposta.json() == {"ok": False, "code": "painel_ausente"}
    assert (await cliente.get("/assets/app.js")).status == 404
    assert (await cliente.get("/health")).status == 200


async def test_byte_nulo_no_caminho_do_asset_e_404_json(cliente):
    for caminho in ("/assets/%00", "/assets/app.js%00.txt"):
        resposta = await cliente.get(caminho)
        assert resposta.status == 404, caminho
        assert await resposta.json() == {"ok": False, "code": "nao_encontrado"}
        assert resposta.headers.get("X-Frame-Options") == "DENY"


async def test_index_que_sumiu_depois_do_boot_responde_503_json(cliente, amb):
    assert (await cliente.get("/")).status == 200
    (amb.dir_painel / "index.html").unlink()
    resposta = await cliente.get("/")
    assert resposta.status == 503
    assert await resposta.json() == {"ok": False, "code": "painel_ausente"}
    assert resposta.headers.get("X-Frame-Options") == "DENY"


async def test_nome_de_arquivo_maior_que_o_sistema_aceita_e_404_json(cliente):
    # Why: a component above NAME_MAX raises ENAMETOOLONG, which used to become a 500.
    # Por que: um componente acima de NAME_MAX estoura ENAMETOOLONG, que virava 500.
    for tamanho in (300, 3000):
        resposta = await cliente.get("/assets/" + "a" * tamanho)
        assert resposta.status == 404, tamanho
        assert await resposta.json() == {"ok": False, "code": "nao_encontrado"}
        assert resposta.headers.get("X-Frame-Options") == "DENY"
