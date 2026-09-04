// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { CartaoEquipamento, usarEquipamentos } from "./Equipamentos.tsx";
import { t, traduzirErro, type Idioma } from "./i18n";
import { caminhoDa, irPara } from "./rotas.ts";

function Voltar() {
  return (
    <a className="voltar" href={caminhoDa({ tela: "inicio" })}>
      <span aria-hidden="true">&larr;</span> {t("voltar_inicio")}
    </a>
  );
}

export default function DetalheEquipamento({
  identidade,
  idioma,
}: {
  identidade: string;
  idioma: Idioma;
}) {
  const { catalogo, lista, erro, recarregar } = usarEquipamentos();
  const equipamento = lista?.find((candidato) => candidato.identidade === identidade);
  return (
    <>
      <Voltar />
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {lista === null && erro === null && <p className="carregando">{t("carregando")}</p>}
      {lista !== null && equipamento === undefined && (
        // Why: an identity the list no longer carries was removed in another session, or the
        // address was typed by hand; either way the honest answer is a sentence and a way
        // back, never a blank screen.
        // Por que: uma identidade que a lista não traz mais foi removida em outra sessão, ou o
        // endereço foi digitado na mão; nos dois casos a resposta honesta é uma frase e um
        // caminho de volta, nunca uma tela em branco.
        <section className="cartao">
          <p>{t("detalhe_nao_encontrado")}</p>
        </section>
      )}
      {equipamento !== undefined && (
        <CartaoEquipamento
          equipamento={equipamento}
          item={(catalogo ?? []).find((candidato) => candidato.tipo === equipamento.tipo)}
          idioma={idioma}
          aoMudar={() => void recarregar()}
          aoRemover={() => irPara({ tela: "inicio" })}
        />
      )}
    </>
  );
}
