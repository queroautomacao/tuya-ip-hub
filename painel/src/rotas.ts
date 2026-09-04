// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the daemon serves one index.html and knows no route of the panel, so the address of a
// screen lives after the hash: a reload, a bookmark and the back button all land on the same
// screen without the daemon learning a single path. No library, because the whole thing is
// a parse and a print, and a library is a dependency the image would carry.
// Por que: o daemon serve um index.html só e não conhece rota nenhuma do painel, então o
// endereço de uma tela vive depois do hash: recarregar, favoritar e o botão de voltar caem
// todos na mesma tela sem o daemon aprender um caminho sequer. Sem biblioteca, porque a coisa
// inteira é um parse e um print, e biblioteca é dependência que a imagem carregaria.

export type Rota =
  | { tela: "inicio" }
  | { tela: "novo" }
  | { tela: "equipamento"; identidade: string }
  | { tela: "zonas" }
  | { tela: "cenas" }
  | { tela: "drivers" }
  | { tela: "conta" };

export type Aba = "inicio" | "zonas" | "cenas" | "drivers" | "conta";

// The tabs of the navigation, in the order they are drawn; the label is an i18n key.
// As abas da navegação, na ordem em que são desenhadas; o rótulo é uma chave de i18n.
export const ABAS: readonly { aba: Aba; rota: Rota; chave: `nav_${Aba}` }[] = [
  { aba: "inicio", rota: { tela: "inicio" }, chave: "nav_inicio" },
  { aba: "zonas", rota: { tela: "zonas" }, chave: "nav_zonas" },
  { aba: "cenas", rota: { tela: "cenas" }, chave: "nav_cenas" },
  { aba: "drivers", rota: { tela: "drivers" }, chave: "nav_drivers" },
  { aba: "conta", rota: { tela: "conta" }, chave: "nav_conta" },
];

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
    // Why: a hash somebody typed by hand can carry a lone percent sign, and that is a route
    // that does not exist, never an exception on the screen.
    // Por que: um hash que alguém digitou na mão pode levar um sinal de porcento solto, e
    // isso é uma rota que não existe, nunca uma exceção na tela.
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
    // Why: "novo" is a screen and never an identity, and an identity the daemon would refuse
    // (empty after decoding) is not worth a screen that says nothing was found.
    // Por que: "novo" é tela e nunca identidade, e uma identidade que o daemon recusaria
    // (vazia depois de decodificar) não merece uma tela dizendo que nada foi achado.
    if (identidade === null || identidade === "") return INICIO;
    return { tela: "equipamento", identidade };
  }
  if (segundo !== undefined) return INICIO;
  if (primeiro === "zonas" || primeiro === "cenas" || primeiro === "drivers" || primeiro === "conta") {
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

// Why: the detail and the registration of an equipment belong to the home tab, so the tab
// stays lit while the operator is inside one of them.
// Por que: o detalhe e o cadastro de um equipamento pertencem à aba de início, então a aba
// continua acesa enquanto o operador está dentro de um deles.
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
