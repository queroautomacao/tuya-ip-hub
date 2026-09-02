# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: minimum of 8, PBKDF2-HMAC-SHA256, 200 thousand iterations, salt per install.

Seção 9: mínimo de 8, PBKDF2-HMAC-SHA256, 200 mil iterações, salt por instalação.
"""

import hashlib
import secrets

import pytest

from iphub import auth
from iphub.auth import ITERACOES, SENHA_MINIMA, TAMANHO_SALT, SenhaCurta, conferir, gerar_hash

SENHA = "senha-de-teste"


@pytest.fixture(scope="module")
def credencial() -> tuple[str, str, int]:
    return gerar_hash(SENHA)


@pytest.mark.parametrize("senha", ["", "a", "1234567", "sete123", "       "])
def test_senha_abaixo_do_minimo_e_recusada(senha):
    assert len(senha) < SENHA_MINIMA
    with pytest.raises(SenhaCurta):
        gerar_hash(senha)


def test_senha_curta_e_um_value_error():
    assert issubclass(SenhaCurta, ValueError)


def test_o_minimo_exato_e_aceito():
    salt_hex, hash_hex, iteracoes = gerar_hash("12345678")
    assert len(salt_hex) == TAMANHO_SALT * 2
    assert len(hash_hex) == 64
    assert iteracoes == ITERACOES


def test_iteracoes_sao_duzentos_mil(credencial):
    assert ITERACOES == 200_000
    assert credencial[2] == 200_000


def test_o_salt_muda_a_cada_chamada():
    primeiro = gerar_hash(SENHA)
    segundo = gerar_hash(SENHA)
    assert primeiro[0] != segundo[0]
    assert primeiro[1] != segundo[1]


def test_mesmo_salt_e_mesma_senha_dao_o_mesmo_hash(credencial):
    salt_hex, hash_hex, iteracoes = credencial
    esperado = hashlib.pbkdf2_hmac(
        "sha256", SENHA.encode("utf-8"), bytes.fromhex(salt_hex), iteracoes
    )
    assert esperado.hex() == hash_hex


def test_a_senha_certa_confere(credencial):
    assert conferir(SENHA, *credencial)


@pytest.mark.parametrize("tentativa", ["senha-de-test", "senha-de-teste ", "SENHA-DE-TESTE", ""])
def test_senha_quase_certa_nao_confere(credencial, tentativa):
    assert not conferir(tentativa, *credencial)


def test_hash_adulterado_nao_confere(credencial):
    salt_hex, hash_hex, iteracoes = credencial
    trocado = ("0" if hash_hex[0] != "0" else "1") + hash_hex[1:]
    assert not conferir(SENHA, salt_hex, trocado, iteracoes)


def test_salt_adulterado_nao_confere(credencial):
    salt_hex, hash_hex, iteracoes = credencial
    trocado = ("0" if salt_hex[0] != "0" else "1") + salt_hex[1:]
    assert not conferir(SENHA, trocado, hash_hex, iteracoes)


@pytest.mark.parametrize("senha", ["", "qualquer-senha"])
def test_hub_sem_senha_nunca_autentica(senha):
    assert not conferir(senha, "", "", 0)
    assert not conferir(senha, "", "", ITERACOES)


@pytest.mark.parametrize(
    ("salt_hex", "hash_hex", "iteracoes"),
    [
        ("zz", "aa" * 32, ITERACOES),
        ("aa" * 16, "abc", ITERACOES),
        ("aa" * 16, "zz" * 32, ITERACOES),
        (None, None, ITERACOES),
        (b"\x00" * 16, b"\x00" * 32, ITERACOES),
        ("aa" * 16, "aa" * 32, 0),
        ("aa" * 16, "aa" * 32, -1),
        ("aa" * 16, "aa" * 32, "200000"),
        ("aa" * 16, "aa" * 32, None),
    ],
)
def test_valor_guardado_malformado_devolve_falso(salt_hex, hash_hex, iteracoes):
    assert conferir(SENHA, salt_hex, hash_hex, iteracoes) is False


def test_baixar_as_iteracoes_no_disco_nao_abre_a_porta(credencial):
    salt_hex, hash_hex, _ = credencial
    assert not conferir(SENHA, salt_hex, hash_hex, 1)


def test_conferir_usa_compare_digest(monkeypatch, credencial):
    original = secrets.compare_digest
    chamadas = []

    def espiao(a, b):
        chamadas.append((a, b))
        return original(a, b)

    monkeypatch.setattr(auth.secrets, "compare_digest", espiao)
    assert conferir(SENHA, *credencial)
    assert len(chamadas) == 1


@pytest.mark.parametrize(
    "senha",
    [
        "\ud800abcdefgh",
        "abcd\ud800efgh",
        "abcdefgh\udfff",
        "𐀀abcdefgh",
    ],
)
def test_senha_com_surrogate_solto_deriva_e_confere(senha):
    # Why: a lone surrogate arrives from JSON; before the guard it raised out of the route
    # as a 500 on /api/posse and /api/senha, unauthenticated in the ownership window.
    # Por que: um surrogate solto chega pelo JSON; antes da guarda ele estourava na rota
    # como 500 em /api/posse e /api/senha, sem autenticação, na janela de posse.
    salt, hash_hex, iteracoes = gerar_hash(senha)
    assert conferir(senha, salt, hash_hex, iteracoes)
    assert not conferir(senha + "x", salt, hash_hex, iteracoes)
