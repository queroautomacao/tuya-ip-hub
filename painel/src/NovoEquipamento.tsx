// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import CadastroEquipamento from "./CadastroEquipamento.tsx";
import { usarEquipamentos } from "./Equipamentos.tsx";
import { t, type Idioma } from "./i18n";
import { caminhoDa, irPara } from "./rotas.ts";

export default function NovoEquipamento({ idioma }: { idioma: Idioma }) {
  const { catalogo } = usarEquipamentos();
  return (
    <>
      <a className="voltar" href={caminhoDa({ tela: "inicio" })}>
        <span aria-hidden="true">&larr;</span> {t("voltar_inicio")}
      </a>
      <div className="tela-cabeca">
        <div>
          <h2>{t("novo_titulo")}</h2>
          <p>{t("novo_intro")}</p>
        </div>
      </div>
      {/* Why: a registration that landed takes the operator back to the list, where the new */}
      {/* card is; staying on an empty form would look like nothing happened. */}
      {/* Por que: um cadastro que entrou leva o operador de volta à lista, onde está o cartão */}
      {/* novo; ficar num formulário vazio pareceria que nada aconteceu. */}
      <CadastroEquipamento
        catalogo={catalogo}
        idioma={idioma}
        aoCadastrar={() => irPara({ tela: "inicio" })}
      />
    </>
  );
}
