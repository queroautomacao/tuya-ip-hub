// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the panel follows the system by default and the operator may pin it light or dark,
// because a rack room is dark and a phone in the sun is not; the choice lives in the
// browser, like the language, and never in the hub.
// Por que: o painel segue o sistema por padrão e o operador pode fixá-lo claro ou escuro,
// porque uma sala de rack é escura e um celular no sol não é; a escolha mora no navegador,
// como o idioma, e nunca no hub.

export const TEMAS = ["auto", "claro", "escuro"] as const;
export type Tema = (typeof TEMAS)[number];

const CHAVE_STORAGE = "iphub.tema";

export function ehTema(valor: unknown): valor is Tema {
  return TEMAS.some((tema) => tema === valor);
}

export function proximoTema(atual: Tema): Tema {
  const posicao = TEMAS.indexOf(atual);
  return TEMAS[(posicao + 1) % TEMAS.length] ?? "auto";
}

// The value the stylesheet reads; the automatic theme leaves the attribute out so the
// media query decides.
// O valor que a folha de estilo lê; o tema automático deixa o atributo de fora para a
// media query decidir.
export function atributoDo(tema: Tema): string | null {
  if (tema === "claro") return "light";
  if (tema === "escuro") return "dark";
  return null;
}

export function lerTema(): Tema {
  try {
    const salvo = window.localStorage.getItem(CHAVE_STORAGE);
    return ehTema(salvo) ? salvo : "auto";
  } catch {
    return "auto";
  }
}

export function aplicarTema(tema: Tema): void {
  const atributo = atributoDo(tema);
  if (atributo === null) delete document.documentElement.dataset.tema;
  else document.documentElement.dataset.tema = atributo;
}

export function definirTema(tema: Tema): void {
  aplicarTema(tema);
  try {
    window.localStorage.setItem(CHAVE_STORAGE, tema);
  } catch {
    // Why: blocked storage (private mode) must not break the switch.
    // Por que: storage bloqueado (modo privado) não pode quebrar a troca.
  }
}
