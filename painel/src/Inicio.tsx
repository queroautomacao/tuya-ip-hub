// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the home is the whole installation at a glance, one card per equipment, and the one
// action it exists for is adding the next one. Everything a card shows comes from the daemon
// and from the manifest of the driver, never from a table of this screen.
// Por que: o início é a instalação inteira de relance, um cartão por equipamento, e a única
// ação para a qual ele existe é adicionar o próximo. Tudo que um cartão mostra vem do daemon e
// do manifesto do driver, nunca de uma tabela desta tela.

import { usarEquipamentos } from "./Equipamentos.tsx";
import { linhasDoEstado, rotuloDoTipo, type Equipamento, type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";
import { caminhoDa } from "./rotas.ts";

function Resumo({ equipamento }: { equipamento: Equipamento }) {
  // Why: the card is a glance, so it carries the readings that mean something without the
  // detail, and leaves the codes and the registration to the screen behind it.
  // Por que: o cartão é um relance, então leva as leituras que dizem algo sem o detalhe, e
  // deixa os códigos e o cadastro para a tela atrás dele.
  const itens = linhasDoEstado(equipamento.estado).flatMap((linha) => {
    if (linha.especie === "logico") return [`${t(`estado_${linha.campo}` as const)}: ${t(linha.logico ? "sim" : "nao")}`];
    if (linha.especie === "numero") return [`${t(`estado_${linha.campo}` as const)}: ${linha.numero}`];
    if (linha.especie === "texto") return [linha.texto];
    return [];
  });
  if (itens.length === 0) return null;
  return (
    <ul className="resumo">
      {itens.slice(0, 4).map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function Cartao({
  equipamento,
  item,
  idioma,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
}) {
  const online = equipamento.estado.online;
  return (
    <li>
      <a
        className={`cartao-link ${online ? "cartao-online" : "cartao-offline"}`}
        href={caminhoDa({ tela: "equipamento", identidade: equipamento.identidade })}
        aria-label={`${t("inicio_ver")}: ${equipamento.nome || equipamento.identidade}`}
      >
        <h3>{equipamento.nome || equipamento.identidade}</h3>
        <p className="tipo">{rotuloDoTipo(item, idioma, equipamento.tipo)}</p>
        <Resumo equipamento={equipamento} />
        <p className="estado-curto">
          <span className="ponto" aria-hidden="true" />
          {online ? t("equipamentos_online") : t("equipamentos_offline")}
        </p>
      </a>
    </li>
  );
}

function Adicionar() {
  return (
    <a className="adicionar" href={caminhoDa({ tela: "novo" })}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg>
      <span>{t("inicio_adicionar")}</span>
    </a>
  );
}

export default function Inicio({ idioma }: { idioma: Idioma }) {
  const { catalogo, lista, erro } = usarEquipamentos();
  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("equipamentos_titulo")}</h2>
          <p>{t("inicio_intro")}</p>
        </div>
        <Adicionar />
      </div>
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {lista === null && erro === null && <p className="carregando">{t("carregando")}</p>}
      {/* Why: section 6, zero equipment is a normal state of the hub and not a failure. */}
      {/* Por que: seção 6, zero equipamento é estado normal do hub e não uma falha. */}
      {lista !== null && lista.length === 0 && (
        <section className="cartao vazio">
          <h3>{t("inicio_vazio_titulo")}</h3>
          <p>{t("inicio_vazio_texto")}</p>
        </section>
      )}
      {lista !== null && lista.length > 0 && (
        <ul className="grade">
          {lista.map((equipamento) => (
            <Cartao
              key={equipamento.identidade}
              equipamento={equipamento}
              item={(catalogo ?? []).find((candidato) => candidato.tipo === equipamento.tipo)}
              idioma={idioma}
            />
          ))}
        </ul>
      )}
    </>
  );
}
