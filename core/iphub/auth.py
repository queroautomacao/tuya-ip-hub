# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Panel password: PBKDF2-HMAC-SHA256, salt per installation,."""

import hashlib
import secrets

# Twelve, and not the eight of the first months: this password is now the last door of a
# hub that can be reached from the internet inside the window of the remote access. A hub
# that was configured before this rule keeps its password and keeps working; what it does not
# get is the remote door, until its owner changes it.
SENHA_MINIMA = 12
ITERACOES = 200_000
TAMANHO_SALT = 16
ALGORITMO = "sha256"


class SenhaCurta(ValueError):
    """The password is shorter than SENHA_MINIMA."""


def _bytes(senha: str) -> bytes:
    return senha.encode("utf-8", errors="surrogatepass")


def gerar_hash(senha: str) -> tuple[str, str, int]:
    """Returns (salt_hex, hash_hex, iteracoes) for a fresh random salt."""
    if len(senha) < SENHA_MINIMA:
        raise SenhaCurta(f"password needs at least {SENHA_MINIMA} characters")
    salt = secrets.token_bytes(TAMANHO_SALT)
    # A lone surrogate arrives from JSON and would raise out of the route as a 500;
    # it is a password like any other, so it is encoded and hashed, never refused by crash.
    derivado = hashlib.pbkdf2_hmac(ALGORITMO, _bytes(senha), salt, ITERACOES)
    return salt.hex(), derivado.hex(), ITERACOES


def conferir(senha: str, salt_hex: str, hash_hex: str, iteracoes: int) -> bool:
    """True only for the right password against a well formed stored value."""
    # A hub with no password, or with a damaged config, must answer no instead of
    # raising into the login route, where an exception would leak that state.
    try:
        salt = bytes.fromhex(salt_hex)
        esperado = bytes.fromhex(hash_hex)
        if not salt or not esperado or iteracoes < 1:
            return False
        derivado = hashlib.pbkdf2_hmac(ALGORITMO, _bytes(senha), salt, iteracoes)
    except (AttributeError, TypeError, ValueError):
        return False
    return secrets.compare_digest(derivado, esperado)


def forte(senha: object) -> bool:
    """Whether a password meets the current minimum, which is what the remote door asks for."""
    return isinstance(senha, str) and len(senha) >= SENHA_MINIMA
