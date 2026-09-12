// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import CadastroEquipamento from "./CadastroEquipamento.tsx";
import { usarEquipamentos } from "./Equipamentos.tsx";
import { t, type Idioma } from "./i18n";
import { caminhoDa, irPara } from "./rotas.ts";

export default function NovoEquipamento({ idioma }: { idioma: Idioma }) {
  const { catalogo, lista } = usarEquipamentos();
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
      {/* A registration that landed takes the operator back to the list, where the new */}
      {/* Card is; staying on an empty form would look like nothing happened. */}
      <CadastroEquipamento
        catalogo={catalogo}
        idioma={idioma}
        aoCadastrar={() => irPara({ tela: "inicio" })}
        enderecos={(lista ?? []).map((equipamento) => equipamento.ip)}
      />
    </>
  );
}
