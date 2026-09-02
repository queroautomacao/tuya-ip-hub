// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

export interface Saude {
  ok: boolean;
  code: string | null;
  versao: string;
  schema_version: number;
  uptime_s: number;
}

export const INTERVALO_MS = 10_000;

// Why: shorter than the poll interval, so two checks never overlap.
// Por que: menor que o intervalo de poll, para duas checagens nunca se sobreporem.
const PRAZO_MS = 5_000;

export function ehSaude(valor: unknown): valor is Saude {
  if (typeof valor !== "object" || valor === null) return false;
  const v = valor as Record<string, unknown>;
  return (
    typeof v.ok === "boolean" &&
    (v.code === null || typeof v.code === "string") &&
    typeof v.versao === "string" &&
    typeof v.schema_version === "number" &&
    typeof v.uptime_s === "number"
  );
}

export async function lerSaude(): Promise<Saude> {
  const resposta = await fetch("/health", {
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal: AbortSignal.timeout(PRAZO_MS),
  });
  const corpo: unknown = await resposta.json().catch(() => null);
  if (ehSaude(corpo)) return corpo;
  throw new Error(resposta.ok ? "corpo_invalido" : `http_${resposta.status}`);
}

export function formatarUptime(segundos: number): string {
  const total = Math.max(0, Math.floor(segundos));
  const dias = Math.floor(total / 86_400);
  const relogio = [
    Math.floor((total % 86_400) / 3_600),
    Math.floor((total % 3_600) / 60),
    total % 60,
  ]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
  return dias > 0 ? `${dias}d ${relogio}` : relogio;
}
