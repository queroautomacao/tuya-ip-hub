# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Panel password: PBKDF2-HMAC-SHA256, salt per installation, section 9.

Senha do painel: PBKDF2-HMAC-SHA256, salt por instalação, seção 9.
"""

import hashlib
import secrets

SENHA_MINIMA = 8
ITERACOES = 200_000
TAMANHO_SALT = 16
ALGORITMO = "sha256"


class SenhaCurta(ValueError):
    """The password is shorter than SENHA_MINIMA.

    A senha é menor que SENHA_MINIMA.
    """


def _bytes(senha: str) -> bytes:
    return senha.encode("utf-8", errors="surrogatepass")


def gerar_hash(senha: str) -> tuple[str, str, int]:
    """Returns (salt_hex, hash_hex, iteracoes) for a fresh random salt.

    Devolve (salt_hex, hash_hex, iteracoes) para um salt aleatório novo.
    """
    if len(senha) < SENHA_MINIMA:
        raise SenhaCurta(f"password needs at least {SENHA_MINIMA} characters")
    salt = secrets.token_bytes(TAMANHO_SALT)
    # Why: a lone surrogate arrives from JSON and would raise out of the route as a 500;
    # it is a password like any other, so it is encoded and hashed, never refused by crash.
    # Por que: um surrogate solto chega pelo JSON e estouraria na rota como 500; ele é uma
    # senha como outra qualquer, então é codificado e derivado, nunca recusado por estouro.
    derivado = hashlib.pbkdf2_hmac(ALGORITMO, _bytes(senha), salt, ITERACOES)
    return salt.hex(), derivado.hex(), ITERACOES


def conferir(senha: str, salt_hex: str, hash_hex: str, iteracoes: int) -> bool:
    """True only for the right password against a well formed stored value.

    Verdadeiro só para a senha certa contra um valor guardado bem formado.
    """
    # Why: a hub with no password, or with a damaged config, must answer no instead of
    # raising into the login route, where an exception would leak that state.
    # Por que: um hub sem senha, ou com config danificada, responde não em vez de estourar
    # na rota de login, onde uma exceção denunciaria esse estado.
    try:
        salt = bytes.fromhex(salt_hex)
        esperado = bytes.fromhex(hash_hex)
        if not salt or not esperado or iteracoes < 1:
            return False
        derivado = hashlib.pbkdf2_hmac(ALGORITMO, _bytes(senha), salt, iteracoes)
    except (AttributeError, TypeError, ValueError):
        return False
    return secrets.compare_digest(derivado, esperado)
