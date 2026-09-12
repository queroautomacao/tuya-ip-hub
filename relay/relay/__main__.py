# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Runs the relay. Everything it needs comes from the environment, and none of it is secret.

RELAY_BASE is the address browsers reach it by, and it is what the hub publishes to the app
of the customer; without it the hub would have to guess the name of a host it never sees.
RELAY_PROXIES names the reverse proxies in front of this process, as addresses or networks
separated by commas, so the address of a hub or of a browser is read from X-Forwarded-For
instead of being the proxy's own every time. RELAY_DIRECTUS_URL, RELAY_DIRECTUS_TOKEN,
RELAY_DIRECTUS_COLECAO and RELAY_DIRECTUS_CAMPO name the registry of homes this relay serves
(see relay/cadastro.py); without the first two any hub that dials is served.
"""

import logging
import os
from urllib.parse import urlsplit

from aiohttp import web

from relay.app import criar_app, redes_de
from relay.cadastro import Cadastro

BIND_PADRAO = "0.0.0.0"
PORTA_PADRAO = 8090


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("RELAY_LOG", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    porta = os.environ.get("RELAY_PORTA") or str(PORTA_PADRAO)
    if not porta.isdigit() or not 1 <= int(porta) <= 65535:
        raise SystemExit(f"RELAY_PORTA must be a port, got {porta!r}")
    base = os.environ.get("RELAY_BASE", "").strip()
    partes = urlsplit(base)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        # Says what arrived, so a compose whose .env lost the variable reads as such and
        # not as a mystery: an empty value here is the usual way this line is reached.
        raise SystemExit(
            "RELAY_BASE must be the public address of this relay, "
            f"like https://remoto.example.com; got {base!r}"
        )
    try:
        proxies = redes_de(os.environ.get("RELAY_PROXIES", ""))
    except ValueError as erro:
        raise SystemExit(f"RELAY_PROXIES must be addresses or networks: {erro}") from None
    directus = os.environ.get("RELAY_DIRECTUS_URL", "").strip()
    token = os.environ.get("RELAY_DIRECTUS_TOKEN", "").strip()
    cadastro = None
    if directus and token:
        try:
            cadastro = Cadastro(
                directus,
                token,
                os.environ.get("RELAY_DIRECTUS_COLECAO", "").strip() or "homes",
                os.environ.get("RELAY_DIRECTUS_CAMPO", "").strip() or "home_id",
            )
        except ValueError as erro:
            raise SystemExit(f"RELAY_DIRECTUS_*: {erro}") from None
        logging.getLogger("relay").info("serving the homes registered in Directus")
    else:
        logging.getLogger("relay").warning(
            "RELAY_DIRECTUS_URL or RELAY_DIRECTUS_TOKEN is not set: "
            "this relay serves ANY hub that dials in"
        )
    web.run_app(
        criar_app(base=base, proxies=proxies, cadastro=cadastro),
        host=os.environ.get("RELAY_BIND") or BIND_PADRAO,
        port=int(porta),
        print=None,
        # No access log. A public address is scanned around the clock by things looking for
        # a Jira, a WordPress and a .env, and a line per attempt buries the handful of lines
        # that matter: a hub that dialled in, one that went away, a frame nobody speaks. All
        # of those are logged by the service itself.
        access_log=None,
    )


if __name__ == "__main__":
    main()
