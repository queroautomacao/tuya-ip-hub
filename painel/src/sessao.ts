// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

export const CHAVE_SESSAO = "iphub.sessao";

// On the local network the panel is the whole origin, and the token may live across tabs
// and reloads. Through the relay every hub of the fleet shares ONE origin, and localStorage
// is shared with it: a panel served for another hub, in another tab, could read the token
// of this one. Served from a sub path, the token lives in the storage of this tab alone.
export function compartilhaOrigem(caminho: string): boolean {
  return caminho.replace(/[^/]*$/, "") !== "/";
}

// Private mode and a storage blocked by policy throw on every access, and a
// panel that forgets the token on reload is far better than one that cannot open.
function deposito(): Storage | null {
  try {
    const porAba =
      typeof window !== "undefined" && compartilhaOrigem(window.location.pathname);
    return (porAba ? globalThis.sessionStorage : globalThis.localStorage) ?? null;
  } catch {
    return null;
  }
}

export function guardar(token: string): void {
  try {
    deposito()?.setItem(CHAVE_SESSAO, token);
  } catch {
    return;
  }
}

export function ler(): string | null {
  try {
    return deposito()?.getItem(CHAVE_SESSAO) || null;
  } catch {
    return null;
  }
}

export function limpar(): void {
  try {
    deposito()?.removeItem(CHAVE_SESSAO);
  } catch {
    return;
  }
}
