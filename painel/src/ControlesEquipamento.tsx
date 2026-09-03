// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: section 6, the buttons of a card are the capabilities the manifest declares and
// nothing else, so the whole control strip is one module the card only places.
// Por que: seção 6, os botões de um cartão são as capacidades que o manifesto declara e
// nada mais, então a faixa de controles é um módulo que o cartão só posiciona.

import { useState } from "react";
import {
  controles,
  prepararAcao,
  type Controle,
  type EstadoEquipamento,
  type Preparo,
} from "./equipamentos.ts";
import { t } from "./i18n";

function EntradaControle({
  controle,
  fontes,
  valor,
  aoMudar,
}: {
  controle: Controle;
  fontes: string[];
  valor: string;
  aoMudar: (novo: string) => void;
}) {
  const rotulo = `${t(`acao_${controle.acao}` as const)} ${t("acao_valor")}`;
  if (controle.especie === "escala") {
    return (
      <input
        className="valor"
        type="number"
        min={0}
        max={100}
        inputMode="numeric"
        aria-label={rotulo}
        value={valor}
        onChange={(evento) => aoMudar(evento.target.value)}
      />
    );
  }
  if (controle.especie === "escolha" && fontes.length > 0) {
    return (
      <select
        className="valor"
        aria-label={rotulo}
        value={valor}
        onChange={(evento) => aoMudar(evento.target.value)}
      >
        <option value="">{t("acao_valor")}</option>
        {fontes.map((fonte) => (
          <option key={fonte} value={fonte}>
            {fonte}
          </option>
        ))}
      </select>
    );
  }
  if (controle.especie === "simples" || controle.especie === "alternar") return null;
  return (
    <input
      className="valor"
      type="text"
      aria-label={rotulo}
      value={valor}
      onChange={(evento) => aoMudar(evento.target.value)}
    />
  );
}

export default function Controles({
  capacidades,
  estado,
  ocupado,
  aoExecutar,
}: {
  capacidades: string[];
  estado: EstadoEquipamento;
  ocupado: boolean;
  aoExecutar: (acao: string, preparo: Preparo) => void;
}) {
  const [valores, setValores] = useState<Record<string, string>>({});
  const lista = controles(capacidades);
  if (lista.length === 0) return null;
  return (
    <div className="controles">
      {lista.map((controle) => (
        <div className="controle" key={controle.acao}>
          <EntradaControle
            controle={controle}
            fontes={estado.fontes}
            valor={valores[controle.acao] ?? ""}
            aoMudar={(novo) => setValores((atual) => ({ ...atual, [controle.acao]: novo }))}
          />
          <button
            type="button"
            className="botao secundario"
            disabled={ocupado}
            onClick={() => {
              const bruto = valores[controle.acao] ?? "";
              aoExecutar(controle.acao, prepararAcao(controle, bruto, estado));
            }}
          >
            {t(`acao_${controle.acao}` as const)}
          </button>
        </div>
      ))}
    </div>
  );
}
