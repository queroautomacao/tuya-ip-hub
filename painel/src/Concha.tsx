// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the shell owns the two things every screen shares, the top bar and the navigation, and
// nothing else; which screen is drawn is decided by the route, so a screen never knows about
// the others.
// Por que: a casca é dona das duas coisas que toda tela compartilha, a barra de cima e a
// navegação, e de mais nada; que tela é desenhada é decidido pela rota, então uma tela nunca
// sabe das outras.

import type { ReactNode } from "react";
import { IDIOMAS, t, type Idioma } from "./i18n";
import { ABAS, abaDa, caminhoDa, type Aba, type Rota } from "./rotas.ts";

function Icone({ aba }: { aba: Aba }) {
  // Why: five strokes drawn inline cost nothing on the wire and carry no font or icon
  // dependency into the image; each is hidden from the screen reader, the label speaks.
  // Por que: cinco traços desenhados inline não custam nada no fio e não levam fonte nem
  // dependência de ícone para a imagem; cada um fica escondido do leitor de tela, o rótulo
  // fala.
  const comum = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (aba) {
    case "inicio":
      return (
        <svg {...comum}>
          <path d="M3 11 12 4l9 7" />
          <path d="M5 10v10h5v-6h4v6h5V10" />
        </svg>
      );
    case "zonas":
      return (
        <svg {...comum}>
          <rect x="5" y="3" width="14" height="18" rx="2" />
          <circle cx="12" cy="14" r="3.5" />
          <circle cx="12" cy="7.5" r="1" />
        </svg>
      );
    case "cenas":
      return (
        <svg {...comum}>
          <path d="M8 5v14l11-7z" />
        </svg>
      );
    case "drivers":
      return (
        <svg {...comum}>
          <path d="m8 8-4 4 4 4" />
          <path d="m16 8 4 4-4 4" />
          <path d="m14 4-4 16" />
        </svg>
      );
    case "conta":
      return (
        <svg {...comum}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21a8 8 0 0 1 16 0" />
        </svg>
      );
  }
}

export default function Concha({
  rota,
  idioma,
  aoTrocarIdioma,
  children,
}: {
  rota: Rota;
  idioma: Idioma;
  aoTrocarIdioma: (idioma: Idioma) => void;
  children: ReactNode;
}) {
  const ativa = abaDa(rota);
  return (
    <div className="concha">
      <header className="barra">
        <h1>{t("titulo")}</h1>
        <nav className="idiomas" aria-label={t("idioma")}>
          {IDIOMAS.map((opcao) => (
            <button
              key={opcao}
              type="button"
              aria-pressed={opcao === idioma}
              onClick={() => aoTrocarIdioma(opcao)}
            >
              {t(`idioma_${opcao}` as const)}
            </button>
          ))}
        </nav>
      </header>
      <nav className="navegacao" aria-label={t("nav_menu")}>
        {ABAS.map(({ aba, rota: destino, chave }) => (
          <a key={aba} href={caminhoDa(destino)} aria-current={aba === ativa ? "page" : undefined}>
            <Icone aba={aba} />
            <span>{t(chave)}</span>
          </a>
        ))}
      </nav>
      <main className="tela">{children}</main>
    </div>
  );
}
