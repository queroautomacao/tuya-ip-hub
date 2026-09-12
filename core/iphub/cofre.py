# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The vault: the secrets of the installation encrypted at rest, with a key bound to the board.

A file born 0600 keeps a secret from another user of the same machine and from nobody who
holds the card: whoever pulls the eMMC or the SD card reads the key of every licence and the
credential of every device in it. What the vault changes is WHERE the key of those secrets
lives. On a board with a serial in its silicon the key is derived from that serial, which is
not on the card, and from a salt that is; the card alone, in another machine, opens nothing.

On a machine with no serial to read (a laptop, a virtual machine, a runner) the key is a file
beside the data, and that protects nothing from whoever holds the card. The log says which of
the two this hub got, once, at boot, so nobody believes in a protection that is not there.

What is encrypted: the key of each licence, the credentials of each equipment, the secret of
the authenticator and the machine credential of the bus. What is not: the hash of the
password, because a hash is already the thing that hides the password and a hub whose board
was swapped must still let its owner in; and the names, the addresses and the scenes, which
are the installation and not a secret of anybody.

A value that this vault cannot open reads as empty and is counted, so the panel can say that
the secrets of this installation were written by another board, and the way back is the
backup, which carries its own key.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from iphub import arquivos

log = logging.getLogger("iphub.cofre")

PREFIXO = "cofre:"
ARQUIVO_SAL = "cofre.sal"
ARQUIVO_CHAVE = "cofre.chave"
SAL_BYTES = 16
CHAVE_BYTES = 32
NONCE_BYTES = 12
_ROTULO = b"tuya-ip-hub cofre v1"

# Where a Linux board writes the serial of its silicon: the line of cpuinfo on the ARM
# kernels, and the device tree on the ones that keep it there instead.
FONTES_DA_SERIE = ("/proc/cpuinfo", "/sys/firmware/devicetree/base/serial-number")
_LINHA_DA_SERIE = re.compile(r"^Serial\s*:\s*([0-9A-Za-z]+)\s*$", re.MULTILINE)


def serie_da_placa(fontes: tuple[str, ...] = FONTES_DA_SERIE) -> str:
    """The serial of the silicon, or an empty string on a machine that has none to read."""
    for fonte in fontes:
        try:
            bruto = Path(fonte).read_bytes()
        except OSError:
            continue
        texto = bruto.decode("utf-8", errors="ignore").replace("\x00", "").strip()
        if fonte.endswith("cpuinfo"):
            achado = _LINHA_DA_SERIE.search(texto)
            if achado is not None and achado.group(1).strip("0"):
                return achado.group(1)
            continue
        if texto and texto.strip("0"):
            return texto
    return ""


def cifrado(valor: object) -> bool:
    return isinstance(valor, str) and valor.startswith(PREFIXO)


class Cofre:
    """The key of the secrets of one data directory, and the two operations over it."""

    def __init__(self, dir_data: Path, serie: str | None = None) -> None:
        self._dir = Path(dir_data)
        arquivos.garantir_diretorio(self._dir)
        # The serial is read once, here, so a test hands one in and a board reads its own.
        serie = serie_da_placa() if serie is None else serie
        sal = self._ler_ou_gerar(ARQUIVO_SAL, SAL_BYTES)
        arquivo_de_chave = self._dir / ARQUIVO_CHAVE
        if serie and not arquivo_de_chave.is_file():
            # The serial is not on the card and the salt is; the key needs both, so a copy
            # of the card in another machine holds only half of it.
            self.presa_ao_hardware = True
            self._chave = hmac.new(sal, _ROTULO + serie.encode("utf-8"), hashlib.sha256).digest()
        else:
            # Nothing of this machine can be told from the card, so the key is a file
            # beside the data; once that file exists it keeps answering, even on a board
            # that could have derived a key, because the secrets were written with it.
            self.presa_ao_hardware = False
            self._chave = self._ler_ou_gerar(ARQUIVO_CHAVE, CHAVE_BYTES)
        # A stable name of this data directory, for the address of the hub on the LAN.
        self.identidade = hashlib.sha256(sal).hexdigest()[:4]
        self.ilegiveis = 0
        self._avisou = False

    def cifrar(self, texto: str) -> str:
        """The value as it is written to disk; an empty value stays empty and visible."""
        if not texto:
            return ""
        nonce = secrets.token_bytes(NONCE_BYTES)
        selado = AESGCM(self._chave).encrypt(nonce, texto.encode("utf-8"), _ROTULO)
        return PREFIXO + base64.urlsafe_b64encode(nonce + selado).decode("ascii")

    def decifrar(self, valor: object) -> str:
        """The value as the daemon uses it; a value written in clear by an older daemon
        passes through, and one this key cannot open reads as empty and is counted."""
        if not isinstance(valor, str):
            return ""
        if not valor.startswith(PREFIXO):
            return valor
        try:
            bruto = base64.urlsafe_b64decode(valor[len(PREFIXO) :].encode("ascii"))
            nonce, selado = bruto[:NONCE_BYTES], bruto[NONCE_BYTES:]
            return AESGCM(self._chave).decrypt(nonce, selado, _ROTULO).decode("utf-8")
        except (ValueError, InvalidTag, UnicodeDecodeError):
            self.ilegiveis += 1
            if not self._avisou:
                self._avisou = True
                log.error(
                    "a secret on disk was written with another key (another board, or a "
                    "vault file that was replaced); it reads as empty until it is typed "
                    "again or a backup is restored"
                )
            return ""

    def _ler_ou_gerar(self, nome: str, tamanho: int) -> bytes:
        caminho = self._dir / nome
        texto = arquivos.ler_texto(caminho)
        if texto:
            try:
                bruto = bytes.fromhex(texto.strip())
            except ValueError:
                bruto = b""
            if len(bruto) == tamanho:
                return bruto
            log.error("%s is not %d bytes of hex; a new one is written", nome, tamanho)
        bruto = secrets.token_bytes(tamanho)
        arquivos.escrever_texto(caminho, bruto.hex() + "\n")
        return bruto


_ABERTOS: dict[Path, Cofre] = {}


def abrir(dir_data: Path) -> Cofre:
    """The vault of a data directory, opened once per process and shared by every reader."""
    chave = Path(dir_data).resolve()
    cofre = _ABERTOS.get(chave)
    if cofre is None:
        cofre = Cofre(chave)
        _ABERTOS[chave] = cofre
        log.info(
            "vault: %s",
            "key bound to the serial of this board"
            if cofre.presa_ao_hardware
            else "no board serial to read, the key is a file beside the data and protects "
            "nothing from whoever holds the card",
        )
    return cofre


def esquecer(dir_data: Path) -> None:
    """Drops the opened vault of a directory, so a test that rewrites the files starts over."""
    _ABERTOS.pop(Path(dir_data).resolve(), None)
