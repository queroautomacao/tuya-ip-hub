// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The home is the whole installation at a glance, one card per equipment, and the one
// action it exists for is adding the next one. Everything a card shows comes from the daemon
// and from the manifest of the driver, never from a table of this screen.

import ControlesRapidos from "./ControlesRapidos.tsx";
import { usarEquipamentos } from "./Equipamentos.tsx";
import {
  linhasDoEstado,
  marcasDoCatalogo,
  rotuloDoTipo,
  type Equipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";
import { logoDa } from "./logos.ts";
import { caminhoDa } from "./rotas.ts";

function Resumo({ equipamento }: { equipamento: Equipamento }) {
  // The card is a glance, so it carries the readings that mean something without the
  // detail, and leaves the codes and the registration to the screen behind it.
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
  aoMudar,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  aoMudar: () => void;
}) {
  const online = equipamento.estado.online;
  // The keys sit OUTSIDE the link, because a button inside an anchor is a key that also
  // opens the screen behind it, and the whole point of these keys is not going there.
  return (
    <li className={`cartao-equipamento ${online ? "cartao-online" : "cartao-offline"}`}>
      <a
        className="cartao-link"
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
      <ControlesRapidos equipamento={equipamento} item={item} aoMudar={aoMudar} />
    </li>
  );
}

// The home is the whole installation at a glance, and what the hub can reach is part of
// that glance: an integrator standing in a house wants to know, before opening a form, whether
// the receiver on the rack is one this hub speaks to. The list comes from the catalog, so a
// driver that lands tomorrow shows up with no screen to edit.
function Marcas({ catalogo }: { catalogo: ItemCatalogo[] | null }) {
  const marcas = marcasDoCatalogo(catalogo ?? []);
  if (marcas.length === 0) return null;
  return (
    <section className="marcas">
      <h2>{t("inicio_drivers")}</h2>
      <ul className="marcas-lista">
        {marcas.map((marca) => {
          const logo = logoDa(marca);
          return (
            <li key={marca} className="marca-suportada">
              {/* The mark keeps the proportion of its own art and is flattened to one */}
              {/* Ink by the stylesheet, so a wordmark stays wide, a glyph stays narrow, all */}
              {/* Of them share a height, and a row of marks of nine makers reads as one */}
              {/* System instead of as a sheet of stickers. The alt carries the name, so a */}
              {/* Reader with no images gets the fact the drawing gives. */}
              {logo === null ? marca : <img className="marca-suportada-logo" src={logo} alt={marca} />}
            </li>
          );
        })}
      </ul>
    </section>
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
  const { catalogo, lista, erro, recarregar } = usarEquipamentos();
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
      {/* Zero equipment is a normal state of the hub and not a failure. */}
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
              aoMudar={() => void recarregar()}
            />
          ))}
        </ul>
      )}
      <Marcas catalogo={catalogo} />
    </>
  );
}
