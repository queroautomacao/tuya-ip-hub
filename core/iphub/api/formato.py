# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The panel's view of the contract, in JSON, and never a device credential.

everything the panel shows about a driver comes from the manifest, so this
module only translates the contract types into the shape the routes answer with. The
enums travel by value, in lower case, because the panel reads them as plain text.
"""

from iphub.config import Cadastro, Item
from iphub.drivers.descoberta import Achado
from iphub.drivers.manifesto import (
    Campo,
    Descoberta,
    Estado,
    Manifesto,
    TipoCampo,
    produto_de,
    template_de,
)


def campo_json(campo: Campo) -> dict:
    return {
        "nome": campo.nome,
        "tipo": campo.tipo.value,
        "obrigatorio": campo.obrigatorio,
        "padrao": campo.padrao,
    }


def descoberta_json(descoberta: Descoberta) -> dict:
    return {
        "ssdp_st": list(descoberta.ssdp_st),
        "ssdp_fabricantes": list(descoberta.ssdp_fabricantes),
        "mdns_servicos": list(descoberta.mdns_servicos),
    }


def manifesto_json(manifesto: Manifesto) -> dict:
    """Both languages travel; the panel picks one, and no key of the manifest is dropped."""
    return {
        "tipo": manifesto.tipo,
        "categoria": manifesto.categoria,
        # The name of a maker is nominative use and travels as text; the panel
        # writes it in its own type and never carries the figurative mark of anybody.
        "marca": manifesto.marca,
        "auth": manifesto.auth.value,
        "capacidades": list(manifesto.capacidades),
        "teclas": list(manifesto.teclas),
        "modos": list(manifesto.modos),
        "ventos": list(manifesto.ventos),
        # A list of the registration starts empty and its values are strings of
        # the protocol of the device; what the driver suggests is what teaches the integrator
        # the shape, so it travels with the manifest and the panel offers it.
        # A cloud driver has no address on the LAN, so the form of the panel
        # asks for the credential and not for an ip that would never be dialled.
        "nuvem": manifesto.nuvem,
        "lista_da_conta": manifesto.lista_da_conta,
        "sugestoes": [
            {"lista": s.lista, "rotulo": s.rotulo, "valor": s.valor} for s in manifesto.sugestoes
        ],
        # The product a type enters and the template its panel draws are
        # decided by the category, and the panel reads them here instead of deciding again.
        "produto": produto_de(manifesto.categoria),
        "template": template_de(manifesto.categoria),
        "rotulo": dict(manifesto.rotulo),
        "textos": {idioma: dict(textos) for idioma, textos in manifesto.textos.items()},
        "config_campos": [campo_json(campo) for campo in manifesto.config_campos],
        "descoberta": descoberta_json(manifesto.descoberta),
    }


def estado_json(estado: Estado) -> dict:
    return {
        "online": estado.online,
        "ligado": estado.ligado,
        "volume": estado.volume,
        "mudo": estado.mudo,
        "fonte": estado.fonte,
        "fontes": list(estado.fontes),
        "modos": list(estado.modos),
        "atalhos": [{"valor": valor, "rotulo": rotulo} for valor, rotulo in _pares(estado.atalhos)],
        "reproduzindo": estado.reproduzindo,
        "tocando": estado.tocando,
        "temperatura": estado.temperatura,
        "modo": estado.modo,
        "vento": estado.vento,
        "detalhe": estado.detalhe,
    }


def equipamento_json(
    cadastro: Cadastro,
    manifesto: Manifesto | None,
    estado: Estado,
    posicao: tuple[str, int] | None = None,
) -> dict:
    """One registration as the panel reads it: the names of the secrets, never their value,
    plus the licence and the number it occupies,.
    """
    return {
        "identidade": cadastro.identidade,
        "tipo": cadastro.tipo,
        "nome": cadastro.nome,
        "ip": cadastro.ip,
        "nivel_maximo": cadastro.nivel_maximo,
        "campos": _campos_publicos(cadastro, manifesto),
        "segredos_definidos": _segredos_definidos(cadastro, manifesto),
        "listas": {
            nome: [item_json(item) for item in itens] for nome, itens in cadastro.listas.items()
        },
        "licenca": None if posicao is None else posicao[0],
        "numero": None if posicao is None else posicao[1],
        "estado": estado_json(estado),
    }


def item_json(item: Item) -> dict:
    return {"rotulo": item.rotulo, "valor": item.valor}


def achado_json(achado: Achado, *, ja_cadastrado: bool) -> dict:
    """What the sweep saw; tipo and identidade are empty strings, never null."""
    # The panel prints both as text, so a null there would print the word null in the
    # list of what the segment answered.
    return {
        "tipo": achado.tipo,
        "identidade": achado.identidade,
        "ip": achado.ip,
        "nome": achado.nome,
        "porta": achado.porta,
        "descricao": achado.descricao,
        "ja_cadastrado": ja_cadastrado,
    }


def _campos_publicos(cadastro: Cadastro, manifesto: Manifesto | None) -> dict[str, str]:
    # The routes keep a SEGREDO in the segredos of the registration, so this filter only
    # matters for a config.json edited by hand; a credential must not leave the daemon
    # because someone typed it in the wrong key.
    if manifesto is None:
        return {}
    segredos = {campo.nome for campo in manifesto.config_campos if campo.tipo is TipoCampo.SEGREDO}
    return {nome: valor for nome, valor in cadastro.campos.items() if nome not in segredos}


def _segredos_definidos(cadastro: Cadastro, manifesto: Manifesto | None) -> list[str]:
    # With no manifest nothing says which key of the registration is a credential, and a
    # filter that cannot tell has to answer nothing: the tipo that left the image is exactly
    # the case where a password sits in campos, and guessing there would hand it to the panel.
    if manifesto is None:
        return []
    return sorted(nome for nome, valor in cadastro.segredos.items() if valor)


def _pares(bruto: object) -> tuple[tuple[str, str], ...]:
    """The (value, label) pairs of a state, ignoring anything that is not one.

    The shape of this field is a contract and a driver may break it; a
    malformed pair is dropped instead of taking the whole reading of the equipment down.
    """
    if not isinstance(bruto, tuple | list):
        return ()
    achados = []
    for item in bruto:
        if not isinstance(item, tuple | list) or len(item) != 2:
            continue
        valor, rotulo = item
        if isinstance(valor, str) and isinstance(rotulo, str) and valor:
            achados.append((valor, rotulo))
    return tuple(achados)
