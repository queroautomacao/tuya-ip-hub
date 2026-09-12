// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// What the owner checks about the box itself: the names it answers by on the network and
// where the key of its secrets lives.

import { useEffect, useState } from "react";
import { codigoDoErro, lerSeguranca, type Seguranca } from "./api";
import { t, traduzirErro } from "./i18n";

function enderecos(seguranca: Seguranca): string[] {
  return seguranca.nomes.map((nome) => `http://${nome}:${seguranca.porta}`);
}

export default function CartaoSeguranca() {
  const [seguranca, setSeguranca] = useState<Seguranca | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    lerSeguranca()
      .then(setSeguranca)
      .catch((falha) => setErro(codigoDoErro(falha)));
  }, []);

  return (
    <section className="cartao">
      <h2>{t("conta_seguranca")}</h2>
      <p className="texto-suave">{t("conta_seguranca_texto")}</p>
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {seguranca !== null && (
        <dl>
          <dt>{t("conta_enderecos")}</dt>
          <dd>
            {enderecos(seguranca).map((endereco) => (
              <div key={endereco}>
                <code>{endereco}</code>
              </div>
            ))}
          </dd>
          <dt>{t("conta_cofre")}</dt>
          <dd>
            {seguranca.cofre.presa_ao_hardware ? t("conta_cofre_placa") : t("conta_cofre_arquivo")}
            {seguranca.cofre.ilegiveis > 0 && (
              <p className="erro" role="alert">
                {t("conta_cofre_ilegiveis")}
              </p>
            )}
          </dd>
        </dl>
      )}
    </section>
  );
}
