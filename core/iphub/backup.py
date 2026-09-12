# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A backup of the installation: one file, encrypted with a password of its own.

What an installation is: the name, the password of its owner (as a hash), the equipment with
their credentials, the licences with their keys, the numbers on the app, the scenes, the
schedules, the second factor and the home of the app. What it is not: the sessions, the
machine credential of the bus and the private key of the panel, which belong to the box and
are born again on the next one.

The file carries its own key, derived from a password the owner types when exporting, and
never the key of the vault of the box it came from: a backup exists to be restored on ANOTHER
board, whose vault has another key. The secrets inside travel in clear inside the sealed
blob, and are sealed again by the vault of whichever board restores them.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from iphub.auth import SENHA_MINIMA
from iphub.config import ITERACOES_MAXIMAS, Config, ConfigIncompativel, de_dados
from iphub.versao import SCHEMA_VERSION

FORMATO = "tuya-ip-hub-backup"
VERSAO = 1
# Three times the iterations of the panel password: a backup is exported once in a while and
# restored rarely, and the file may sit in an e-mail for years.
ITERACOES = 600_000
ITERACOES_MINIMAS = 100_000
SAL_BYTES = 16
NONCE_BYTES = 12
CHAVE_BYTES = 32
EXTENSAO = ".iphub"


class BackupInvalido(ValueError):
    """The file is not a backup this daemon reads."""


class SenhaErrada(ValueError):
    """The password does not open this backup."""


def exportar(cfg: Config, senha: str, agora=time.time) -> bytes:
    """The file, as bytes. Raises ValueError for a password under the minimum."""
    if not isinstance(senha, str) or len(senha) < SENHA_MINIMA:
        raise ValueError(f"the password of a backup needs at least {SENHA_MINIMA} characters")
    sal = secrets.token_bytes(SAL_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    conteudo = json.dumps(
        {"schema_version": SCHEMA_VERSION, **asdict(cfg)}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    selado = AESGCM(_chave(senha, sal, ITERACOES)).encrypt(nonce, conteudo, FORMATO.encode())
    envelope = {
        "formato": FORMATO,
        "versao": VERSAO,
        "criado_em": int(agora()),
        "iteracoes": ITERACOES,
        "sal": sal.hex(),
        "nonce": nonce.hex(),
        "dados": base64.b64encode(selado).decode("ascii"),
    }
    return (json.dumps(envelope, indent=2) + "\n").encode("utf-8")


def importar(bruto: bytes, senha: str) -> Config:
    """The configuration inside the file. Raises BackupInvalido or SenhaErrada."""
    try:
        envelope = json.loads(bruto.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as erro:
        raise BackupInvalido("the file is not a backup") from erro
    if not isinstance(envelope, dict) or envelope.get("formato") != FORMATO:
        raise BackupInvalido("the file is not a backup")
    if envelope.get("versao") != VERSAO:
        raise BackupInvalido(f"the backup is version {envelope.get('versao')!r}, not {VERSAO}")
    iteracoes = envelope.get("iteracoes")
    # A file that named a billion iterations would hang the daemon on the restore, and one
    # that named ten would be a file somebody weakened; both are refused before any work.
    if (
        not isinstance(iteracoes, int)
        or isinstance(iteracoes, bool)
        or not ITERACOES_MINIMAS <= iteracoes <= ITERACOES_MAXIMAS
    ):
        raise BackupInvalido("the backup names iterations outside the band")
    try:
        sal = bytes.fromhex(envelope.get("sal", ""))
        nonce = bytes.fromhex(envelope.get("nonce", ""))
        selado = base64.b64decode(envelope.get("dados", ""), validate=True)
    except (ValueError, TypeError) as erro:
        raise BackupInvalido("the backup is damaged") from erro
    if len(sal) != SAL_BYTES or len(nonce) != NONCE_BYTES or not selado:
        raise BackupInvalido("the backup is damaged")
    if not isinstance(senha, str):
        raise SenhaErrada("no password")
    try:
        conteudo = AESGCM(_chave(senha, sal, iteracoes)).decrypt(nonce, selado, FORMATO.encode())
    except InvalidTag as erro:
        # A wrong password and a blob somebody edited look the same to the cipher, and
        # that is the right answer: neither opens the file.
        raise SenhaErrada("the password does not open this backup") from erro
    try:
        dados = json.loads(conteudo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as erro:
        raise BackupInvalido("the backup is damaged") from erro
    if not isinstance(dados, dict) or dados.get("schema_version") != SCHEMA_VERSION:
        raise BackupInvalido(
            f"the backup carries schema_version {dados.get('schema_version')!r}, "
            f"this daemon expects {SCHEMA_VERSION}"
        )
    try:
        return de_dados(dados)
    except ConfigIncompativel as erro:
        raise BackupInvalido(f"the backup is not a configuration: {erro}") from erro


def _chave(senha: str, sal: bytes, iteracoes: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8", errors="surrogatepass"), sal, iteracoes, CHAVE_BYTES
    )
