// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import en from "./en.json";
import pt from "./pt.json";

export type Idioma = "pt" | "en";
export type Chave = keyof typeof pt;

export const IDIOMAS: readonly Idioma[] = ["pt", "en"];

const CHAVE_STORAGE = "iphub.idioma";

// Why: typing every dictionary against the pt key set turns a missing key
// into a compile error instead of a blank label at runtime.
// Por que: tipar todo dicionário pelo conjunto de chaves do pt transforma
// chave faltante em erro de compilação, não em rótulo vazio em runtime.
const DICIONARIOS: Record<Idioma, Record<Chave, string>> = { pt, en };

function ehIdioma(valor: unknown): valor is Idioma {
  return valor === "pt" || valor === "en";
}

function lerPreferencia(): Idioma | null {
  try {
    const salvo = window.localStorage.getItem(CHAVE_STORAGE);
    return ehIdioma(salvo) ? salvo : null;
  } catch {
    return null;
  }
}

function aplicarNoDocumento(idioma: Idioma): void {
  document.documentElement.lang = idioma === "pt" ? "pt-BR" : "en";
}

export function detectarIdioma(): Idioma {
  const salvo = lerPreferencia();
  if (salvo) return salvo;
  return navigator.language.toLowerCase().startsWith("pt") ? "pt" : "en";
}

let atual: Idioma = detectarIdioma();
aplicarNoDocumento(atual);

export function idiomaAtual(): Idioma {
  return atual;
}

export function definirIdioma(novo: Idioma): void {
  atual = novo;
  aplicarNoDocumento(novo);
  try {
    window.localStorage.setItem(CHAVE_STORAGE, novo);
  } catch {
    // Why: blocked storage (private mode) must not break the language switch.
    // Por que: storage bloqueado (modo privado) não pode quebrar a troca de idioma.
  }
}

export function t(chave: Chave): string {
  return DICIONARIOS[atual][chave];
}
