// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Before the owner is in there is no navigation to draw, so this is the whole page: the
// brand and one card with the only thing to do. The photo is the room the product serves, and
// it is a background and never a wall the form has to fight: the card keeps the tokens of the
// theme and lets a little of the room through, so it reads the same light or dark.

import type { ReactNode } from "react";
import { BotaoTema, Idiomas } from "./Concha.tsx";
import fundo from "./entrada.jpg";
import { t, type Idioma } from "./i18n";
import marca from "./marca.png";
import type { Tema } from "./tema.ts";

export default function Porta({
  idioma,
  tema,
  aoTrocarIdioma,
  aoTrocarTema,
  rodape,
  children,
}: {
  idioma: Idioma;
  tema: Tema;
  aoTrocarIdioma: (idioma: Idioma) => void;
  aoTrocarTema: () => void;
  rodape: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="porta" style={{ backgroundImage: `url(${fundo})` }}>
      <div className="porta-veu" aria-hidden="true" />
      <div className="porta-conteudo">
        <div className="porta-acoes">
          <BotaoTema tema={tema} aoTrocar={aoTrocarTema} />
          <Idiomas idioma={idioma} aoTrocar={aoTrocarIdioma} />
        </div>
        <div className="porta-grade">
          <header className="porta-marca">
            <img src={marca} alt="" width={64} height={64} />
            <h1>{t("produto")}</h1>
          </header>
          <main className="porta-cartao">{children}</main>
        </div>
        {rodape}
      </div>
    </div>
  );
}
