// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the shell owns the things every screen shares, the brand, the language, the theme and
// the navigation, and nothing else; which screen is drawn is decided by the route, so a
// screen never knows about the others.
// Por que: a casca é dona do que toda tela compartilha, a marca, o idioma, o tema e a
// navegação, e de mais nada; que tela é desenhada é decidido pela rota, então uma tela nunca
// sabe das outras.

import type { ReactNode } from "react";
import { IDIOMAS, t, type Idioma } from "./i18n";
import marca from "./marca.png";
import { abaDa, caminhoDa, type Aba, type AbaDoMenu, type Rota } from "./rotas.ts";
import { formatarUptime } from "./saude";
import type { Tema } from "./tema.ts";
import { usarSaude } from "./usarSaude.ts";

const TRACO = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

function Icone({ aba }: { aba: Aba }) {
  // Why: a few strokes drawn inline cost nothing on the wire and carry no font or icon
  // dependency into the image; each is hidden from the screen reader, the label speaks.
  // Por que: uns traços desenhados inline não custam nada no fio e não levam fonte nem
  // dependência de ícone para a imagem; cada um fica escondido do leitor de tela, o rótulo
  // fala.
  switch (aba) {
    case "inicio":
      return (
        <svg {...TRACO}>
          <path d="M3 11 12 4l9 7" />
          <path d="M5 10v10h5v-6h4v6h5V10" />
        </svg>
      );
    case "cenas":
      return (
        <svg {...TRACO}>
          <path d="M8 5v14l11-7z" />
        </svg>
      );
    case "simulador":
      return (
        <svg {...TRACO}>
          <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
          <path d="M10.5 5h3" />
          <circle cx="12" cy="18" r="0.8" />
        </svg>
      );
    case "drivers":
      return (
        <svg {...TRACO}>
          <path d="m8 8-4 4 4 4" />
          <path d="m16 8 4 4-4 4" />
          <path d="m14 4-4 16" />
        </svg>
      );
    case "conta":
      return (
        <svg {...TRACO}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21a8 8 0 0 1 16 0" />
        </svg>
      );
  }
}

function IconeTema({ tema }: { tema: Tema }) {
  if (tema === "claro") {
    return (
      <svg {...TRACO}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  }
  if (tema === "escuro") {
    return (
      <svg {...TRACO}>
        <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
      </svg>
    );
  }
  return (
    <svg {...TRACO}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function Marca({ subtitulo }: { subtitulo: string }) {
  // Why: the brand of the company that sells the appliance, above the name of the
  // installation it serves.
  // Por que: a marca da empresa que vende o appliance, sobre o nome da instalação que ele
  // serve.
  return (
    <a className="marca" href={caminhoDa({ tela: "inicio" })} aria-label={t("produto")}>
      <img className="marca-logo" src={marca} alt="" width={40} height={40} />
      <span className="marca-texto">
        <strong>{t("produto")}</strong>
        <small>{subtitulo}</small>
      </span>
    </a>
  );
}

export function Idiomas({
  idioma,
  aoTrocar,
}: {
  idioma: Idioma;
  aoTrocar: (idioma: Idioma) => void;
}) {
  return (
    <nav className="idiomas" aria-label={t("idioma")}>
      {IDIOMAS.map((opcao) => (
        <button
          key={opcao}
          type="button"
          aria-pressed={opcao === idioma}
          aria-label={t(`idioma_${opcao}` as const)}
          onClick={() => aoTrocar(opcao)}
        >
          {t(`idioma_${opcao}_curto` as const)}
        </button>
      ))}
    </nav>
  );
}

export function BotaoTema({ tema, aoTrocar }: { tema: Tema; aoTrocar: () => void }) {
  const rotulo = `${t("tema")}: ${t(`tema_${tema}` as const)}`;
  return (
    <button type="button" className="tema-botao" aria-label={rotulo} title={rotulo} onClick={aoTrocar}>
      <IconeTema tema={tema} />
    </button>
  );
}

// Why: the state of the daemon and the way out live at the foot of the rail on a desktop,
// where they are always in view; a phone has no rail, so the account screen carries them.
// Por que: o estado do daemon e a saída moram no pé do trilho no desktop, onde estão sempre à
// vista; um celular não tem trilho, então a tela de conta os carrega.
function RodapeDoTrilho({ aoSair }: { aoSair: () => void }) {
  const { fase, saude } = usarSaude();
  return (
    <div className="trilho-rodape">
      <p className={`trilho-estado cartao-${fase}`}>
        <span className="ponto" aria-hidden="true" />
        <span>
          {t("conta_firmware")} {saude ? saude.versao : t("indisponivel")}
        </span>
      </p>
      <p className="trilho-detalhe">
        {t(`estado_${fase}` as const)}
        {saude ? ` · ${t("uptime")} ${formatarUptime(saude.uptime_s)}` : ""}
      </p>
      <button type="button" className="botao secundario" onClick={aoSair}>
        {t("sair")}
      </button>
    </div>
  );
}

export default function Concha({
  rota,
  idioma,
  tema,
  abas,
  subtitulo,
  aoTrocarIdioma,
  aoTrocarTema,
  aoSair,
  children,
}: {
  rota: Rota;
  idioma: Idioma;
  tema: Tema;
  abas: readonly AbaDoMenu[];
  subtitulo: string;
  aoTrocarIdioma: (idioma: Idioma) => void;
  aoTrocarTema: () => void;
  aoSair: () => void;
  children: ReactNode;
}) {
  const ativa = abaDa(rota);
  return (
    <div className="concha">
      <header className="barra">
        <Marca subtitulo={subtitulo} />
        <div className="barra-acoes">
          <BotaoTema tema={tema} aoTrocar={aoTrocarTema} />
          <Idiomas idioma={idioma} aoTrocar={aoTrocarIdioma} />
        </div>
      </header>
      <nav className="navegacao" aria-label={t("nav_menu")}>
        {abas.map(({ aba, rota: destino, chave, ativa: podeAbrir }) =>
          podeAbrir ? (
            <a key={aba} href={caminhoDa(destino)} aria-current={aba === ativa ? "page" : undefined}>
              <Icone aba={aba} />
              <span>{t(chave)}</span>
            </a>
          ) : (
            <span key={aba} className="desativada" aria-disabled="true" title={t("nav_precisa_equipamento")}>
              <Icone aba={aba} />
              <span>{t(chave)}</span>
            </span>
          ),
        )}
        <RodapeDoTrilho aoSair={aoSair} />
      </nav>
      <main className="tela">{children}</main>
    </div>
  );
}
