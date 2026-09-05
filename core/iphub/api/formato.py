# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The panel's view of the contract of section 6, in JSON, and never a device credential.

Section 6: everything the panel shows about a driver comes from the manifest, so this
module only translates the contract types into the shape the routes answer with. The
enums travel by value, in lower case, because the panel reads them as plain text.

A visão do painel do contrato da seção 6, em JSON, e nunca uma credencial de aparelho.

Seção 6: tudo que o painel mostra de um driver vem do manifesto, então este módulo só
traduz os tipos do contrato para a forma com que as rotas respondem. Os enums viajam por
valor, em minúsculas, porque o painel os lê como texto puro.
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
    """Both languages travel; the panel picks one, and no key of the manifest is dropped.

    Os dois idiomas viajam; o painel escolhe um, e nenhuma chave do manifesto é descartada.
    """
    return {
        "tipo": manifesto.tipo,
        "categoria": manifesto.categoria,
        "motor": manifesto.motor,
        "auth": manifesto.auth.value,
        "capacidades": list(manifesto.capacidades),
        "teclas": list(manifesto.teclas),
        "modos": list(manifesto.modos),
        "ventos": list(manifesto.ventos),
        # Why: section 8, a list of the registration starts empty and its values are strings of
        # the protocol of the device; what the driver suggests is what teaches the integrator
        # the shape, so it travels with the manifest and the panel offers it.
        # Por que: seção 8, uma lista do cadastro nasce vazia e os valores dela são strings do
        # protocolo do aparelho; o que o driver sugere é o que ensina a forma ao integrador,
        # então viaja com o manifesto e o painel a oferece.
        # Why: section 1, a cloud driver has no address on the LAN, so the form of the panel
        # asks for the credential and not for an ip that would never be dialled.
        # Por que: seção 1, um driver de nuvem não tem endereço na LAN, então o formulário do
        # painel pede a credencial e não um ip que nunca seria discado.
        "nuvem": manifesto.nuvem,
        "sugestoes": [
            {"lista": s.lista, "rotulo": s.rotulo, "valor": s.valor} for s in manifesto.sugestoes
        ],
        # Why: section 8, the product a type enters and the template its panel draws are
        # decided by the category, and the panel reads them here instead of deciding again.
        # Por que: seção 8, o produto em que um tipo entra e o template que o painel dele
        # desenha são decididos pela categoria, e o painel os lê aqui em vez de decidir de novo.
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
    plus the licence and the number it occupies, section 8.

    Um cadastro como o painel o lê: os nomes dos segredos, nunca o valor deles, mais a licença
    e o número que ele ocupa, seção 8.
    """
    return {
        "identidade": cadastro.identidade,
        "tipo": cadastro.tipo,
        "nome": cadastro.nome,
        "ip": cadastro.ip,
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
    """What the sweep saw; tipo and identidade are empty strings, never null.

    O que a varredura viu; tipo e identidade são textos vazios, nunca nulos.
    """
    # Why: the panel prints both as text, so a null there would print the word null in the
    # list of what the segment answered.
    # Por que: o painel imprime os dois como texto, então um null ali imprimiria a palavra
    # null na lista do que o segmento respondeu.
    return {
        "tipo": achado.tipo,
        "identidade": achado.identidade,
        "ip": achado.ip,
        "porta": achado.porta,
        "descricao": achado.descricao,
        "ja_cadastrado": ja_cadastrado,
    }


def _campos_publicos(cadastro: Cadastro, manifesto: Manifesto | None) -> dict[str, str]:
    # Why: the routes keep a SEGREDO in the segredos of the registration, so this filter only
    # matters for a config.json edited by hand; a credential must not leave the daemon
    # because someone typed it in the wrong key.
    # Por que: as rotas guardam um SEGREDO nos segredos do cadastro, então este filtro só
    # importa para um config.json editado à mão; uma credencial não pode sair do daemon
    # porque alguém a digitou na chave errada.
    if manifesto is None:
        return {}
    segredos = {campo.nome for campo in manifesto.config_campos if campo.tipo is TipoCampo.SEGREDO}
    return {nome: valor for nome, valor in cadastro.campos.items() if nome not in segredos}


def _segredos_definidos(cadastro: Cadastro, manifesto: Manifesto | None) -> list[str]:
    # Why: with no manifest nothing says which key of the registration is a credential, and a
    # filter that cannot tell has to answer nothing: the tipo that left the image is exactly
    # the case where a password sits in campos, and guessing there would hand it to the panel.
    # Por que: sem manifesto nada diz qual chave do cadastro é credencial, e um filtro que não
    # sabe tem de responder nada: o tipo que saiu da imagem é justamente o caso em que a senha
    # está em campos, e adivinhar ali a entregaria ao painel.
    if manifesto is None:
        return []
    return sorted(nome for nome, valor in cadastro.segredos.items() if valor)
