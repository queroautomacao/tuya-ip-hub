# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6: the catalog is walked, never listed by hand, and a broken manifest never ships.

Seção 6: o catálogo é varrido, nunca listado na mão, e um manifesto quebrado nunca embarca.
"""

import functools
import importlib
import pkgutil
from collections.abc import Iterator
from types import ModuleType

from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto, validar

PACOTE_NATIVOS = "iphub.drivers.nativos"


class CatalogoInvalido(ValueError):
    """Two drivers claim the same tipo, which is a test error and never a runtime choice.

    Dois drivers reivindicam o mesmo tipo, que é erro de teste e nunca escolha em runtime.
    """


@functools.cache
def carregar() -> dict[str, type[Driver]]:
    """Every native driver of the image, keyed by tipo and sorted, built once per process.

    Todo driver nativo da imagem, chaveado por tipo e ordenado, montado uma vez por processo.
    """
    # Seam: the declarative catalog of section 7 joins this dict here, as a second source of
    # types, when milestone 3 lands. Nothing is built for it now.
    # Costura: o catálogo declarativo da seção 7 entra neste dict aqui, como segunda fonte de
    # tipos, quando o marco 3 chegar. Nada é construído para ele agora.
    return carregar_pacote(importlib.import_module(PACOTE_NATIVOS))


def esquecer() -> None:
    """Drops the cached catalog, for a test that swaps the package under it.

    Descarta o catálogo em cache, para um teste que troca o pacote por baixo dele.
    """
    carregar.cache_clear()


def carregar_pacote(pacote: ModuleType) -> dict[str, type[Driver]]:
    """Imports every module of the package and collects the Driver subclasses it declares.

    Importa todo módulo do pacote e recolhe as subclasses de Driver que ele declara.
    """
    catalogo: dict[str, type[Driver]] = {}
    origem: dict[str, str] = {}
    for modulo in _modulos(pacote):
        for classe in _drivers(modulo):
            tipo = _tipo_de(classe)
            anterior = catalogo.get(tipo)
            # Why: a module that imports a driver of another module to extend it exports the
            # same class object, and one class is one driver, not a duplicate tipo.
            # Por que: um módulo que importa um driver de outro módulo para estendê-lo exporta
            # o mesmo objeto de classe, e uma classe é um driver, não um tipo duplicado.
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
        # Why: the manifest has to be declared by the class itself; a subclass that only
        # inherits one is a variation of a driver, not a second entry in the catalog.
        # Por que: o manifesto tem de ser declarado pela própria classe; uma subclasse que só
        # herda um é variação de um driver, não uma segunda entrada no catálogo.
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
