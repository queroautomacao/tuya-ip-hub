# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The second factor: time based one time passwords, RFC 6238, with nothing installed.

An authenticator on a phone and this file agree on one shared secret and on the clock, and
the six digits are HMAC-SHA1 over the number of thirty second steps since the epoch. That is
the whole algorithm, it is a page of the standard, and writing it here costs less than a
dependency that would have to be reviewed, pinned, audited and carried into the image.

Two rules that are not in the arithmetic and matter more than it:

- a code is accepted ONCE. The window is one step to each side, which is what a phone whose
  clock drifted needs, and a code that was already spent is refused even inside that window,
  because a code read over somebody's shoulder is a code that works for thirty seconds;
- the secret never leaves this daemon after the enrolment. It is shown once, while the owner
  is pointing the phone at it, and after that only the fingerprint of the codes it produces
  ever travels.
"""

import base64
import hashlib
import hmac
import secrets
from urllib.parse import quote

# Twenty bytes is the length RFC 4226 names for the shared secret, and it is what every
# authenticator expects to read out of a QR code.
SEGREDO_BYTES = 20
DIGITOS = 6
PASSO_S = 30
# One step each way: a phone that is half a minute off still works, and a code stays good
# for at most a minute and a half instead of the ten some implementations allow.
JANELA = 1

EMISSOR = "Tuya IP Hub"


def gerar_segredo() -> str:
    """A fresh shared secret, in the base32 an authenticator reads."""
    return base64.b32encode(secrets.token_bytes(SEGREDO_BYTES)).decode("ascii").rstrip("=")


def passo_de(agora: float) -> int:
    return int(agora // PASSO_S)


def codigo_de(segredo: str, passo: int) -> str:
    """The six digits of one step, or an empty string for a secret that is not base32."""
    bruto = _bytes_do_segredo(segredo)
    if not bruto:
        return ""
    digerido = hmac.new(bruto, passo.to_bytes(8, "big"), hashlib.sha1).digest()
    # The dynamic truncation of the standard: the low nibble of the last byte picks where
    # the four bytes of the code start.
    inicio = digerido[-1] & 0x0F
    valor = int.from_bytes(digerido[inicio : inicio + 4], "big") & 0x7FFFFFFF
    return str(valor % 10**DIGITOS).zfill(DIGITOS)


def conferir(segredo: str, informado: object, agora: float, ultimo_passo: int) -> int | None:
    """The step this code belongs to, or None. A step already spent is never accepted again.

    The caller keeps the step this returns and hands it back as ultimo_passo, which is what
    turns "the code is right" into "the code is right and is not the one used a moment ago".
    """
    if not isinstance(informado, str):
        return None
    limpo = "".join(c for c in informado if c.isdigit())
    if len(limpo) != DIGITOS:
        return None
    atual = passo_de(agora)
    for passo in range(atual - JANELA, atual + JANELA + 1):
        if passo <= ultimo_passo:
            continue
        esperado = codigo_de(segredo, passo)
        # Compared in constant time: the answer of this function is public and the secret
        # behind it is not, and a comparison that stops at the first wrong digit says where.
        if esperado and secrets.compare_digest(esperado, limpo):
            return passo
    return None


def uri(segredo: str, conta: str, emissor: str = EMISSOR) -> str:
    """The otpauth URI an authenticator reads from a QR code."""
    rotulo = quote(f"{emissor}:{conta}".strip(":"), safe="")
    return (
        f"otpauth://totp/{rotulo}?secret={segredo}&issuer={quote(emissor, safe='')}"
        f"&algorithm=SHA1&digits={DIGITOS}&period={PASSO_S}"
    )


def _bytes_do_segredo(segredo: str) -> bytes:
    if not isinstance(segredo, str) or not segredo:
        return b""
    texto = segredo.strip().replace(" ", "").upper()
    # Base32 arrives from a file that a person may have edited, and padding it back is
    # what makes a secret written without the equals signs still readable.
    falta = (-len(texto)) % 8
    try:
        return base64.b32decode(texto + "=" * falta, casefold=False)
    except (ValueError, TypeError):
        return b""
