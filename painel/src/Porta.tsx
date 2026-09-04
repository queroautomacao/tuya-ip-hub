// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: before the owner is in there is no navigation to draw, so this is the whole page: the
// brand and one card with the only thing to do. The photo is the room the product serves, and
// it is a background and never a wall the form has to fight: the card keeps the tokens of the
// theme and lets a little of the room through, so it reads the same light or dark.
// Por que: antes de o dono entrar não há navegação para desenhar, então isto é a página
// inteira: a marca e um cartão com a única coisa a fazer. A foto é a sala que o produto serve,
// e ela é fundo e nunca uma parede contra a qual o formulário lute: o cartão mantém os tokens
// do tema e deixa passar um pouco da sala, então ele se lê igual claro ou escuro.

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
