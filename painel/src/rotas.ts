// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The daemon serves one index.html and knows no route of the panel, so the address of a
// screen lives after the hash: a reload, a bookmark and the back button all land on the same
// screen without the daemon learning a single path. No library, because the whole thing is
// a parse and a print, and a library is a dependency the image would carry.

export type Rota =
  | { tela: "inicio" }
  | { tela: "novo" }
  | { tela: "equipamento"; identidade: string }
  | { tela: "cenas" }
  | { tela: "simulador" }
  | { tela: "log" }
  | { tela: "conta" };

export type Aba = "inicio" | "cenas" | "simulador" | "log" | "conta";

// The tabs of the navigation, in the order they are drawn; the label is an i18n key.
export const ABAS: readonly { aba: Aba; rota: Rota; chave: `nav_${Aba}` }[] = [
  { aba: "inicio", rota: { tela: "inicio" }, chave: "nav_inicio" },
  { aba: "cenas", rota: { tela: "cenas" }, chave: "nav_cenas" },
  { aba: "simulador", rota: { tela: "simulador" }, chave: "nav_simulador" },
  { aba: "log", rota: { tela: "log" }, chave: "nav_log" },
  { aba: "conta", rota: { tela: "conta" }, chave: "nav_conta" },
];

// The scenes and the simulator only set data points of equipment, so the two tabs exist
// once an equipment is registered and not before; a screen of empty numbers is a question the
// operator cannot yet answer.
export const ABAS_DE_EQUIPAMENTO: readonly Aba[] = ["cenas", "simulador"];

export interface AbaDoMenu {
  aba: Aba;
  rota: Rota;
  chave: `nav_${Aba}`;
  ativa: boolean;
}

// A tab that exists but cannot be used yet is drawn dimmed and not taken away, so the
// operator learns what the hub does before the first equipment is registered.
export function abasDoMenu(temEquipamento: boolean): AbaDoMenu[] {
  return ABAS.map((entrada) => ({
    ...entrada,
    ativa: temEquipamento || !ABAS_DE_EQUIPAMENTO.includes(entrada.aba),
  }));
}

const INICIO: Rota = { tela: "inicio" };

function segmentos(hash: string): string[] {
  const caminho = hash.replace(/^#/, "").replace(/^\/+/, "").replace(/\/+$/, "");
  if (caminho === "") return [];
  return caminho.split("/");
}

function decodificar(bruto: string): string | null {
  try {
    return decodeURIComponent(bruto);
  } catch {
    // A hash somebody typed by hand can carry a lone percent sign, and that is a route
    // that does not exist, never an exception on the screen.
    return null;
  }
}

export function lerRota(hash: string): Rota {
  const partes = segmentos(hash);
  if (partes.length === 0) return INICIO;
  const [primeiro, segundo, ...resto] = partes;
  if (resto.length > 0) return INICIO;
  if (primeiro === "equipamentos") {
    if (segundo === undefined) return INICIO;
    if (segundo === "novo") return { tela: "novo" };
    const identidade = decodificar(segundo);
    // "novo" is a screen and never an identity, and an identity the daemon would refuse
    // (empty after decoding) is not worth a screen that says nothing was found.
    if (identidade === null || identidade === "") return INICIO;
    return { tela: "equipamento", identidade };
  }
  if (segundo !== undefined) return INICIO;
  if (
    primeiro === "cenas" ||
    primeiro === "simulador" ||
    primeiro === "log" ||
    primeiro === "conta"
  ) {
    return { tela: primeiro };
  }
  return INICIO;
}

export function caminhoDa(rota: Rota): string {
  switch (rota.tela) {
    case "inicio":
      return "#/";
    case "novo":
      return "#/equipamentos/novo";
    case "equipamento":
      return `#/equipamentos/${encodeURIComponent(rota.identidade)}`;
    default:
      return `#/${rota.tela}`;
  }
}

// The detail and the registration of an equipment belong to the home tab, so the tab
// stays lit while the operator is inside one of them.
export function abaDa(rota: Rota): Aba {
  if (rota.tela === "novo" || rota.tela === "equipamento") return "inicio";
  return rota.tela;
}

export function irPara(rota: Rota): void {
  window.location.hash = caminhoDa(rota);
}

export function rotaAtual(): Rota {
  return lerRota(window.location.hash);
}
