# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The catalog is walked, never listed by hand.

The drivers of the image are the modules of the nativos package, imported one by one and
collected by the manifest each one declares. Nothing else feeds the catalog: the declarative
engine of the former was removed on 7/set/2026, because a driver is code that a
device on the bench proved, and a file of data cannot convert a volume scale, wait for a set
to boot, or read a fact from the answer that already carries it.
"""

import functools
import importlib
import logging
import pkgutil
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto, validar

log = logging.getLogger("iphub.drivers.catalogo")

PACOTE_NATIVOS = "iphub.drivers.nativos"


class CatalogoInvalido(ValueError):
    """Two modules claim one tipo, or a class carries something that is not a manifest."""


class Catalogo:
    """The drivers of the image, read only, handed to the manager and to the routes.

    Why it is still a class: the routes and the manager take one object that answers
    .drivers, and a test hands it the drivers of the hub it is attacking instead of the
    whole image, which is what the nativos argument is for.
    """

    def __init__(
        self, dir_data: Path | None = None, *, nativos: dict[str, type[Driver]] | None = None
    ) -> None:
        del dir_data
        self._nativos = (
            carregar_pacote(importlib.import_module(PACOTE_NATIVOS))
            if nativos is None
            else dict(nativos)
        )

    @property
    def drivers(self) -> dict[str, type[Driver]]:
        return self._nativos

    @property
    def nativos(self) -> dict[str, type[Driver]]:
        return self._nativos


@functools.cache
def carregar() -> dict[str, type[Driver]]:
    """Every driver the IMAGE carries, built once per process."""
    return Catalogo().drivers


def esquecer() -> None:
    """Drops the cached catalog, for a test that swaps the package under it."""
    carregar.cache_clear()


def carregar_pacote(pacote: ModuleType) -> dict[str, type[Driver]]:
    """Imports every module of the package and collects the Driver subclasses it declares."""
    catalogo: dict[str, type[Driver]] = {}
    origem: dict[str, str] = {}
    for modulo in _modulos(pacote):
        for classe in _drivers(modulo):
            tipo = _tipo_de(classe)
            anterior = catalogo.get(tipo)
            # A module that imports a driver of another module to extend it exports the
            # same class object, and one class is one driver, not a duplicate tipo.
            if anterior is classe:
                continue
            if anterior is not None:
                raise CatalogoInvalido(
                    f"tipo {tipo!r} is claimed by {origem[tipo]}.{anterior.__name__} and by "
                    f"{modulo.__name__}.{classe.__name__}"
                )
            catalogo[tipo] = classe
            origem[tipo] = modulo.__name__
    return dict(sorted(catalogo.items()))


def _modulos(pacote: ModuleType) -> Iterator[ModuleType]:
    for info in pkgutil.iter_modules(pacote.__path__, f"{pacote.__name__}."):
        if info.ispkg or info.name.rpartition(".")[2].startswith("_"):
            continue
        yield importlib.import_module(info.name)


def _drivers(modulo: ModuleType) -> Iterator[type[Driver]]:
    for objeto in vars(modulo).values():
        # The manifest has to be declared by the class itself; a subclass that only
        # inherits one is a variation of a driver, not a second entry in the catalog.
        if isinstance(objeto, type) and issubclass(objeto, Driver) and "MANIFESTO" in vars(objeto):
            yield objeto


def _tipo_de(classe: type[Driver]) -> str:
    manifesto = classe.MANIFESTO
    if not isinstance(manifesto, Manifesto):
        raise CatalogoInvalido(
            f"{classe.__module__}.{classe.__name__}.MANIFESTO must be a Manifesto, found "
            f"{type(manifesto).__name__}"
        )
    validar(manifesto)
    return manifesto.tipo
