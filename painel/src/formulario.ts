// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the registration form and the edit form send the same body to the same daemon, so
// the rules that body obeys live here once, and both screens only render them.
// Por que: o formulário de cadastro e o de edição mandam o mesmo corpo ao mesmo daemon,
// então as regras que o corpo obedece moram aqui uma vez, e as telas só as desenham.

import type { Achado, Campo, Equipamento, ItemCatalogo, Listas } from "./equipamentos.ts";

// Why: the daemon refuses a longer or a non printable text with campo_invalido, and it
// measures the length in code points the way python len does.
// Por que: o daemon recusa texto maior ou não imprimível com campo_invalido, e mede o
// comprimento em pontos de código como o len do python.
export const LIMITE_TEXTO = 200;

// Why: python calls a character non printable when it is in the Other or the Separator
// category, the plain space excepted; matching that keeps the panel and the daemon on the
// same answer instead of the daemon refusing after a round trip.
// Por que: o python chama não imprimível o caractere das categorias Other ou Separator,
// exceto o espaço comum; seguir isso mantém painel e daemon na mesma resposta em vez de o
// daemon recusar depois de uma ida e volta.
const NAO_IMPRIMIVEL = /\p{C}|\p{Z}/u;

export function imprimivel(texto: string): boolean {
  return [...texto].every((caractere) => caractere === " " || !NAO_IMPRIMIVEL.test(caractere));
}

export function cabe(texto: string): boolean {
  return [...texto].length <= LIMITE_TEXTO && imprimivel(texto);
}

// Why: 45 characters is the longest IPv6 text form; past it, it is not an address.
// Por que: 45 caracteres é a maior forma textual de IPv6; além disso, não é endereço.
const MAXIMO_IP = 45;

function ehIpv4(texto: string): boolean {
  const partes = texto.split(".");
  if (partes.length !== 4) return false;
  // Why: a leading zero is the octal trick and the daemon refuses it as well, so the
  // panel never accepts an address the daemon then answers ip_invalido to.
  // Por que: zero à esquerda é o truque do octal e o daemon também recusa, então o
  // painel nunca aceita um endereço que o daemon depois responde ip_invalido.
  const valida = (parte: string): boolean =>
    /^\d{1,3}$/.test(parte) && (parte === "0" || !parte.startsWith("0")) && Number(parte) <= 255;
  return partes.every(valida);
}

function ehIpv6(texto: string): boolean {
  if (!texto.includes(":")) return false;
  const lados = texto.split("::");
  if (lados.length > 2) return false;
  const comprimido = lados.length === 2;
  const grupos = [
    ...(lados[0] ? lados[0].split(":") : []),
    ...(comprimido && lados[1] ? lados[1].split(":") : []),
  ];
  let total = grupos.length;
  const ultimo = grupos[grupos.length - 1];
  if (ultimo !== undefined && ultimo.includes(".")) {
    if (!ehIpv4(ultimo)) return false;
    grupos.pop();
    total += 1;
  }
  if (!grupos.every((grupo) => /^[0-9a-fA-F]{1,4}$/.test(grupo))) return false;
  return comprimido ? total < 8 : total === 8;
}

// Why: section 9, the ip is an address and never a name, a URL or a host with a port.
// The daemon is the authority; refusing here puts the rule where the integrator types.
// Por que: seção 9, o ip é endereço e nunca nome, URL ou host com porta. O daemon é a
// autoridade; recusar aqui põe a regra onde o integrador digita.
export function ipLiteral(texto: string): boolean {
  if (!texto || texto.length > MAXIMO_IP) return false;
  return ehIpv4(texto) || ehIpv6(texto);
}

export interface Formulario {
  tipo: string;
  identidade: string;
  nome: string;
  ip: string;
  campos: Record<string, string>;
  // Why: a blank secret keeps the credential the daemon already stores, so erasing one
  // has to be an explicit ask and never a field the operator left alone.
  // Por que: um segredo em branco mantém a credencial que o daemon já guarda, então
  // apagar uma precisa ser pedido explícito e nunca campo que o operador não tocou.
  apagar: readonly string[];
}

export interface CorpoCadastro {
  tipo: string;
  identidade: string;
  nome: string;
  ip: string;
  campos: Record<string, string>;
  // Why: the lists of section 8 are edited on their own card, so the registration form never
  // sends them and the daemon keeps what it stores when the key is absent.
  // Por que: as listas da seção 8 são editadas num cartão próprio, então o formulário de
  // cadastro nunca as manda e o daemon mantém o que guarda quando a chave está ausente.
  listas?: Listas;
}

export type Validacao =
  | { ok: true; corpo: CorpoCadastro }
  | { ok: false; codigo: string; campo: string };

export const VAZIO: Formulario = {
  tipo: "",
  identidade: "",
  nome: "",
  ip: "",
  campos: {},
  apagar: [],
};

// Why: the manifest carries the default of every field it declares, so the form offers
// it and the integrator only types what is particular to the installation.
// Por que: o manifesto carrega o padrão de todo campo que declara, então o formulário o
// oferece e o integrador só digita o que é particular da instalação.
export function padroes(item: ItemCatalogo | undefined): Record<string, string> {
  const declarados = item?.config_campos ?? [];
  const visiveis = declarados.filter((campo) => campo.tipo !== "segredo");
  return Object.fromEntries(visiveis.map((campo) => [campo.nome, campo.padrao]));
}

// Why: the edit form starts from what the daemon stores, and a secret starts blank
// because its value never leaves the daemon.
// Por que: o formulário de edição parte do que o daemon guarda, e um segredo começa em
// branco porque o valor dele nunca sai do daemon.
export function formularioDe(equipamento: Equipamento, item: ItemCatalogo | undefined): Formulario {
  const campos = { ...padroes(item), ...equipamento.campos };
  for (const campo of item?.config_campos ?? []) {
    if (campo.tipo === "segredo") delete campos[campo.nome];
  }
  return {
    tipo: equipamento.tipo,
    identidade: equipamento.identidade,
    nome: equipamento.nome,
    ip: equipamento.ip,
    campos,
    apagar: [],
  };
}

function recusar(codigo: string, campo: string): Validacao {
  return { ok: false, codigo, campo };
}

function bruto(formulario: Formulario, campo: Campo): string {
  return formulario.campos[campo.nome] ?? "";
}

// Why: a SEGREDO is stored verbatim by the daemon, so a device password with a leading or
// a trailing space has to travel as it was typed; trimming it corrupts the credential and
// the device then answers that it is wrong.
// Por que: um SEGREDO é guardado literal pelo daemon, então uma senha de aparelho com
// espaço na ponta precisa viajar como foi digitada; aparar corrompe a credencial e o
// aparelho depois responde que ela está errada.
function valorDoCampo(formulario: Formulario, campo: Campo): string {
  return campo.tipo === "segredo" ? bruto(formulario, campo) : bruto(formulario, campo).trim();
}

function anexar(
  campos: Record<string, string>,
  campo: Campo,
  formulario: Formulario,
  guardados: readonly string[],
): boolean {
  const segredo = campo.tipo === "segredo";
  if (segredo && formulario.apagar.includes(campo.nome)) {
    if (campo.obrigatorio) return false;
    campos[campo.nome] = "";
    return true;
  }
  if (!cabe(bruto(formulario, campo))) return false;
  const valor = valorDoCampo(formulario, campo);
  if (!valor) {
    // Why: an omitted field leaves the manifest default to the daemon, and an omitted
    // secret keeps the stored one; only an obligatory field with nothing stored has to be
    // filled here.
    // Por que: campo omitido deixa o padrão do manifesto com o daemon, e segredo omitido
    // mantém o guardado; só campo obrigatório sem nada guardado precisa ser preenchido.
    return !campo.obrigatorio || (segredo && guardados.includes(campo.nome));
  }
  if (campo.tipo === "inteiro" && !/^-?\d+$/.test(valor)) return false;
  campos[campo.nome] = valor;
  return true;
}

export function validarCadastro(
  formulario: Formulario,
  catalogo: readonly ItemCatalogo[],
  guardados: readonly string[] = [],
): Validacao {
  const item = catalogo.find((candidato) => candidato.tipo === formulario.tipo);
  if (item === undefined) return recusar("tipo_desconhecido", "tipo");
  if (!cabe(formulario.identidade) || !formulario.identidade.trim()) {
    return recusar("campo_invalido", "identidade");
  }
  if (!cabe(formulario.nome)) return recusar("campo_invalido", "nome");
  const ip = formulario.ip.trim();
  if (!ipLiteral(ip)) return recusar("ip_invalido", "ip");
  const campos: Record<string, string> = {};
  for (const campo of item.config_campos) {
    if (!anexar(campos, campo, formulario, guardados)) {
      return recusar("campo_invalido", campo.nome);
    }
  }
  const corpo = {
    tipo: item.tipo,
    identidade: formulario.identidade.trim(),
    nome: formulario.nome.trim(),
    ip,
    campos,
  };
  return { ok: true, corpo };
}

export type OfertaAchado = "cadastrar" | "ja_cadastrado" | "sem_tipo" | "sem_identidade";

// Why: identity is the key of a registration (section 6) and the sweep is the only place
// it could come from, so a device that answered without one cannot be prefilled: the
// operator has no way to invent it, and a form they cannot finish is worse than a note.
// Por que: a identidade é a chave do cadastro (seção 6) e a varredura é o único lugar de
// onde ela viria, então um aparelho que respondeu sem identidade não pode ser preenchido:
// o operador não tem como inventá-la, e um formulário que ele não termina é pior que um
// aviso.
export function ofertaDoAchado(achado: Achado): OfertaAchado {
  if (achado.ja_cadastrado) return "ja_cadastrado";
  if (!achado.tipo) return "sem_tipo";
  if (!achado.identidade) return "sem_identidade";
  return "cadastrar";
}
