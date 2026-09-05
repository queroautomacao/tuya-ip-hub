// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useCallback, useEffect, useState } from "react";
import { CartaoEquipamento, usarEquipamentos } from "./Equipamentos.tsx";
import ListasDoPainel from "./ListasDoPainel.tsx";
import NumeroNoApp from "./NumeroNoApp.tsx";
import { lerLicencas } from "./api.ts";
import { INTERVALO_MS } from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";
import { onde, type Licenca } from "./licencas.ts";
import { caminhoDa, irPara } from "./rotas.ts";

// Why: section 14, a speaker that follows a master has its volume and transport routed to
// it, and the controls say so; the role comes from the book of licences of the daemon and
// is read here so every card of the screen reads the same fact.
// Por que: seção 14, uma caixa que segue um mestre tem o volume e o transporte roteados para
// ele, e os controles dizem isso; o papel vem do livro de licenças do daemon e é lido aqui
// para todo cartão da tela ler o mesmo fato.
function usarLicencas(): Licenca[] {
  const [licencas, setLicencas] = useState<Licenca[]>([]);
  const recarregar = useCallback(async (): Promise<void> => {
    try {
      setLicencas((await lerLicencas()).licencas);
    } catch {
      // Why: the role is a hint on the controls; a listing that failed leaves the hint out
      // and the cycle tries again on the next tick.
      // Por que: o papel é uma dica nos controles; uma listagem que falhou deixa a dica de
      // fora e o ciclo tenta de novo no tique seguinte.
    }
  }, []);
  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);
  return licencas;
}

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
  const licencas = usarLicencas();
  const equipamento = lista?.find((candidato) => candidato.identidade === identidade);
  const papel = onde(licencas, identidade)?.numero.papel ?? "";
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
        <>
          <CartaoEquipamento
            equipamento={equipamento}
            item={(catalogo ?? []).find((candidato) => candidato.tipo === equipamento.tipo)}
            idioma={idioma}
            papel={papel}
            aoMudar={() => void recarregar()}
            aoRemover={() => irPara({ tela: "inicio" })}
          />
          <NumeroNoApp
            equipamento={equipamento}
            item={(catalogo ?? []).find((candidato) => candidato.tipo === equipamento.tipo)}
          />
          <ListasDoPainel
            equipamento={equipamento}
            item={(catalogo ?? []).find((candidato) => candidato.tipo === equipamento.tipo)}
            aoMudar={() => void recarregar()}
          />
        </>
      )}
    </>
  );
}
