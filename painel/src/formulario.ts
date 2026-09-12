// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The registration form and the edit form send the same body to the same daemon, so
// the rules that body obeys live here once, and both screens only render them.

import type { Achado, Campo, Equipamento, ItemCatalogo, Listas } from "./equipamentos.ts";

// The daemon refuses a longer or a non printable text with campo_invalido, and it
// measures the length in code points the way python len does.
export const LIMITE_TEXTO = 200;

// Python calls a character non printable when it is in the Other or the Separator
// category, the plain space excepted; matching that keeps the panel and the daemon on the
// same answer instead of the daemon refusing after a round trip.
const NAO_IMPRIMIVEL = /\p{C}|\p{Z}/u;

export function imprimivel(texto: string): boolean {
  return [...texto].every((caractere) => caractere === " " || !NAO_IMPRIMIVEL.test(caractere));
}

export function cabe(texto: string): boolean {
  return [...texto].length <= LIMITE_TEXTO && imprimivel(texto);
}

// 45 characters is the longest IPv6 text form; past it, it is not an address.
const MAXIMO_IP = 45;

function ehIpv4(texto: string): boolean {
  const partes = texto.split(".");
  if (partes.length !== 4) return false;
  // A leading zero is the octal trick and the daemon refuses it as well, so the
  // panel never accepts an address the daemon then answers ip_invalido to.
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

// The ip is an address and never a name, a URL or a host with a port.
// The daemon is the authority; refusing here puts the rule where the integrator types.
export function ipLiteral(texto: string): boolean {
  if (!texto || texto.length > MAXIMO_IP) return false;
  return ehIpv4(texto) || ehIpv6(texto);
}

export interface Formulario {
  tipo: string;
  identidade: string;
  nome: string;
  ip: string;
  // Typed as text, sent as a number from 1 to 100; the ceiling of the level.
  nivel_maximo: string;
  campos: Record<string, string>;
  // A blank secret keeps the credential the daemon already stores, so erasing one
  // has to be an explicit ask and never a field the operator left alone.
  apagar: readonly string[];
}

export interface CorpoCadastro {
  tipo: string;
  identidade: string;
  nome: string;
  // A registration of a driver of the cloud of a maker carries no address at
  // all, and the daemon refuses one that does; every other registration always carries it.
  ip?: string;
  // The ceiling of the level; absent keeps what the daemon stores, which is what the
  // card of the lists sends, because it edits nothing else.
  nivel_maximo?: number;
  campos: Record<string, string>;
  // The lists are edited on their own card, so the registration form never
  // sends them and the daemon keeps what it stores when the key is absent.
  listas?: Listas;
}

export type Validacao =
  | { ok: true; corpo: CorpoCadastro }
  | { ok: false; codigo: string; campo: string };

export const NIVEL_MAXIMO = 100;

export const VAZIO: Formulario = {
  tipo: "",
  identidade: "",
  nome: "",
  ip: "",
  nivel_maximo: String(NIVEL_MAXIMO),
  campos: {},
  apagar: [],
};

// The manifest carries the default of every field it declares, so the form offers
// it and the integrator only types what is particular to the installation.
export function padroes(item: ItemCatalogo | undefined): Record<string, string> {
  const declarados = item?.config_campos ?? [];
  const visiveis = declarados.filter((campo) => campo.tipo !== "segredo");
  return Object.fromEntries(visiveis.map((campo) => [campo.nome, campo.padrao]));
}

// The edit form starts from what the daemon stores, and a secret starts blank
// because its value never leaves the daemon.
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
    nivel_maximo: String(equipamento.nivel_maximo),
    campos,
    apagar: [],
  };
}

export function nivelMaximo(texto: string): number | null {
  if (!/^\d{1,3}$/.test(texto.trim())) return null;
  const valor = Number(texto.trim());
  return valor >= 1 && valor <= NIVEL_MAXIMO ? valor : null;
}

function recusar(codigo: string, campo: string): Validacao {
  return { ok: false, codigo, campo };
}

function bruto(formulario: Formulario, campo: Campo): string {
  return formulario.campos[campo.nome] ?? "";
}

// A SEGREDO is stored verbatim by the daemon, so a device password with a leading or
// a trailing space has to travel as it was typed; trimming it corrupts the credential and
// the device then answers that it is wrong.
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
    // An omitted field leaves the manifest default to the daemon, and an omitted
    // secret keeps the stored one; only an obligatory field with nothing stored has to be
    // filled here.
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
  // A driver of the cloud of a maker has no address on the LAN, so the form
  // neither asks for one nor sends one; every other driver keeps the literal ip rule exactly.
  const ip = item.nuvem ? "" : formulario.ip.trim();
  if (!item.nuvem && !ipLiteral(ip)) return recusar("ip_invalido", "ip");
  const nivel_maximo = nivelMaximo(formulario.nivel_maximo);
  if (nivel_maximo === null) return recusar("campo_invalido", "nivel_maximo");
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
    ...(item.nuvem ? {} : { ip }),
    nivel_maximo,
    campos,
  };
  return { ok: true, corpo };
}

export type OfertaAchado = "cadastrar" | "ja_cadastrado" | "sem_tipo" | "sem_identidade";

// Identity is the key of a registration and the sweep is the only place
// it could come from, so a device that answered without one cannot be prefilled: the
// operator has no way to invent it, and a form they cannot finish is worse than a note.
export function ofertaDoAchado(achado: Achado): OfertaAchado {
  if (achado.ja_cadastrado) return "ja_cadastrado";
  if (!achado.tipo) return "sem_tipo";
  if (!achado.identidade) return "sem_identidade";
  return "cadastrar";
}
