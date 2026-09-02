# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Process environment: where to listen and where the data and the panel live.

Ambiente do processo: onde escutar e onde ficam os dados e o painel.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BIND_PADRAO = "0.0.0.0"
PORTA_PADRAO = 8080
DIR_DATA_PADRAO = Path("/data")
DIR_PAINEL_PADRAO = Path("/app/painel")


@dataclass(frozen=True)
class Ambiente:
    bind: str
    porta: int
    dir_data: Path
    dir_painel: Path

    @classmethod
    def do_ambiente(cls, env: Mapping[str, str] | None = None) -> "Ambiente":
        """Build from IPHUB_* variables; a bad IPHUB_PORTA raises ValueError.

        Constrói a partir das variáveis IPHUB_*; IPHUB_PORTA inválida levanta ValueError.
        """
        if env is None:
            env = os.environ
        # Why: an empty value in a compose file means "unset", not a request for port zero.
        # Por que: valor vazio num arquivo compose significa "não definido", não porta zero.
        return cls(
            bind=env.get("IPHUB_BIND") or BIND_PADRAO,
            porta=_porta(env.get("IPHUB_PORTA") or str(PORTA_PADRAO)),
            dir_data=Path(env.get("IPHUB_DATA") or DIR_DATA_PADRAO),
            dir_painel=Path(env.get("IPHUB_PAINEL") or DIR_PAINEL_PADRAO),
        )


def _porta(texto: str) -> int:
    # Why: int() also accepts "1_000", "+80" and full-width digits; a port is plain decimal.
    # Por que: int() aceita "1_000", "+80" e dígito de largura dupla; porta é decimal puro.
    texto = texto.strip()
    if not re.fullmatch(r"[0-9]{1,5}", texto) or not 1 <= int(texto) <= 65535:
        raise ValueError(f"IPHUB_PORTA must be an integer from 1 to 65535, got {texto!r}")
    return int(texto)
