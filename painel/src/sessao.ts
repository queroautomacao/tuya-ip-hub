// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

export const CHAVE_SESSAO = "iphub.sessao";

// Why: private mode and a storage blocked by policy throw on every access, and a
// panel that forgets the token on reload is far better than one that cannot open.
// Por que: modo privado e storage bloqueado por política estouram em todo acesso,
// e um painel que esquece o token ao recarregar é bem melhor que um que não abre.
function deposito(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
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
