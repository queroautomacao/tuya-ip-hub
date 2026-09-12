# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Process environment: where to listen and where the data and the panel live."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BIND_PADRAO = "0.0.0.0"
PORTA_PADRAO = 8080
NOME_PADRAO = "iphub"
DIR_DATA_PADRAO = Path("/data")
DIR_PAINEL_PADRAO = Path("/app/painel")


@dataclass(frozen=True)
class Ambiente:
    bind: str
    porta: int
    dir_data: Path
    dir_painel: Path
    # The relay of the maker: ONE address for a whole fleet, so it is set on the image or on
    # the appliance and nobody types it at an installation. It is not a credential: what the
    # relay checks is the home of the app this hub belongs to, and the door is decided here.
    remoto_relay: str = ""
    # The name the hub answers by on mDNS (iphub.local), and whether it answers at all.
    nome: str = NOME_PADRAO
    mdns: bool = True

    @classmethod
    def do_ambiente(cls, env: Mapping[str, str] | None = None) -> "Ambiente":
        """Build from IPHUB_* variables; a bad IPHUB_PORTA raises ValueError."""
        if env is None:
            env = os.environ
        # An empty value in a compose file means "unset", not a request for port zero.
        return cls(
            bind=env.get("IPHUB_BIND") or BIND_PADRAO,
            porta=_porta(env.get("IPHUB_PORTA") or str(PORTA_PADRAO)),
            dir_data=Path(env.get("IPHUB_DATA") or DIR_DATA_PADRAO),
            dir_painel=Path(env.get("IPHUB_PAINEL") or DIR_PAINEL_PADRAO),
            remoto_relay=(env.get("IPHUB_REMOTO_RELAY") or "").strip(),
            nome=_nome(env.get("IPHUB_NOME") or NOME_PADRAO),
            mdns=(env.get("IPHUB_MDNS") or "1").strip() not in ("0", "nao", "no", "false"),
        )


def _porta(texto: str) -> int:
    # Int also accepts "1_000", "+80" and full-width digits; a port is plain decimal.
    texto = texto.strip()
    if not re.fullmatch(r"[0-9]{1,5}", texto) or not 1 <= int(texto) <= 65535:
        raise ValueError(f"IPHUB_PORTA must be an integer from 1 to 65535, got {texto!r}")
    return int(texto)


def _nome(texto: str) -> str:
    # One label of a host name: letters, digits and hyphens, the way every resolver reads it.
    texto = texto.strip().lower()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?", texto):
        raise ValueError(f"IPHUB_NOME must be a host name label, got {texto!r}")
    return texto
