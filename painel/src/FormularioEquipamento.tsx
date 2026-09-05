// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: registering and correcting an equipment ask for the same fields of the same
// manifest, so one component renders both and the two screens cannot drift apart.
// Por que: cadastrar e corrigir um equipamento pedem os mesmos campos do mesmo
// manifesto, então um componente desenha os dois e as telas não podem divergir.

import { Fragment } from "react";
import { textoDoManifesto, type Campo, type ItemCatalogo } from "./equipamentos.ts";
import type { Formulario } from "./formulario.ts";
import { t, type Chave, type Idioma } from "./i18n";

const ROTULOS_FIXOS: Record<string, Chave> = {
  tipo: "cadastro_tipo",
  identidade: "cadastro_identidade",
  nome: "cadastro_nome",
  ip: "cadastro_ip",
};

// Why: the operator has to be told WHICH field the daemon or the panel refused, and the
// name of a declared field comes from the manifest like every other text (section 6).
// Por que: o operador precisa saber QUAL campo o daemon ou o painel recusou, e o nome de
// um campo declarado vem do manifesto como todo texto (seção 6).
export function rotuloDoCampo(
  item: ItemCatalogo | undefined,
  idioma: Idioma,
  nome: string,
): string {
  const fixo = ROTULOS_FIXOS[nome];
  if (fixo !== undefined) return t(fixo);
  return textoDoManifesto(item, idioma, `campo_${nome}`) || nome;
}

function EntradaCampo({
  campo,
  formulario,
  guardados,
  item,
  idioma,
  prefixo,
  aoMudar,
}: {
  campo: Campo;
  formulario: Formulario;
  guardados: readonly string[];
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  prefixo: string;
  aoMudar: (proximo: Formulario) => void;
}) {
  const id = `${prefixo}-campo-${campo.nome}`;
  const segredo = campo.tipo === "segredo";
  const guardado = guardados.includes(campo.nome);
  const apagando = formulario.apagar.includes(campo.nome);
  return (
    <Fragment>
      <label htmlFor={id}>{rotuloDoCampo(item, idioma, campo.nome)}</label>
      <input
        id={id}
        name={campo.nome}
        // Why: a SEGREDO is a device credential, so it is typed hidden and the browser
        // never offers to remember it next to a panel password.
        // Por que: um SEGREDO é credencial de aparelho, então é digitado oculto e o
        // navegador nunca se oferece para lembrá-lo junto de uma senha do painel.
        type={segredo ? "password" : "text"}
        inputMode={campo.tipo === "inteiro" ? "numeric" : undefined}
        required={campo.obrigatorio && !(segredo && (guardado || apagando))}
        disabled={apagando}
        autoComplete={segredo ? "new-password" : "off"}
        value={formulario.campos[campo.nome] ?? ""}
        onChange={(evento) =>
          aoMudar({
            ...formulario,
            campos: { ...formulario.campos, [campo.nome]: evento.target.value },
          })
        }
      />
      {segredo && guardado && <p className="dica">{t("segredo_guardado")}</p>}
      {segredo && guardado && !campo.obrigatorio && (
        <label className="caixa" htmlFor={`${id}-apagar`}>
          <input
            id={`${id}-apagar`}
            type="checkbox"
            checked={apagando}
            onChange={(evento) =>
              aoMudar({
                ...formulario,
                apagar: evento.target.checked
                  ? [...formulario.apagar, campo.nome]
                  : formulario.apagar.filter((nome) => nome !== campo.nome),
              })
            }
          />
          {t("segredo_apagar")}
        </label>
      )}
    </Fragment>
  );
}

export default function FormularioEquipamento({
  item,
  idioma,
  formulario,
  guardados,
  prefixo,
  fixarIdentidade,
  aoMudar,
}: {
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  formulario: Formulario;
  guardados: readonly string[];
  prefixo: string;
  fixarIdentidade: boolean;
  aoMudar: (proximo: Formulario) => void;
}) {
  return (
    <>
      <label htmlFor={`${prefixo}-identidade`}>{t("cadastro_identidade")}</label>
      <input
        id={`${prefixo}-identidade`}
        name="identidade"
        type="text"
        required
        autoComplete="off"
        spellCheck={false}
        // Why: the identity is the key of the registration, so an edit shows it and never
        // lets it change; changing it would be a different equipment.
        // Por que: a identidade é a chave do cadastro, então uma edição a mostra e nunca
        // deixa trocá-la; trocá-la seria outro equipamento.
        readOnly={fixarIdentidade}
        value={formulario.identidade}
        onChange={(evento) => aoMudar({ ...formulario, identidade: evento.target.value })}
      />
      {!fixarIdentidade && <p className="dica">{t("cadastro_identidade_dica")}</p>}
      <label htmlFor={`${prefixo}-nome`}>{t("cadastro_nome")}</label>
      <input
        id={`${prefixo}-nome`}
        name="nome"
        type="text"
        autoComplete="off"
        value={formulario.nome}
        onChange={(evento) => aoMudar({ ...formulario, nome: evento.target.value })}
      />
      {/* Why: section 1, a driver of the cloud of a maker is reached over the internet and */}
      {/* has no address on this network, so asking for one would be a field nobody can fill. */}
      {/* Por que: seção 1, um driver da nuvem de um fabricante é alcançado pela internet e */}
      {/* não tem endereço nesta rede, então pedir um seria campo que ninguém preenche. */}
      {item?.nuvem === true ? (
        <p className="dica">{t("cadastro_na_nuvem")}</p>
      ) : (
        <>
          <label htmlFor={`${prefixo}-ip`}>{t("cadastro_ip")}</label>
          <input
            id={`${prefixo}-ip`}
            name="ip"
            type="text"
            required
            autoComplete="off"
            spellCheck={false}
            value={formulario.ip}
            onChange={(evento) => aoMudar({ ...formulario, ip: evento.target.value })}
          />
        </>
      )}
      {(item?.config_campos ?? []).map((campo) => (
        <EntradaCampo
          key={campo.nome}
          campo={campo}
          formulario={formulario}
          guardados={guardados}
          item={item}
          idioma={idioma}
          prefixo={prefixo}
          aoMudar={aoMudar}
        />
      ))}
    </>
  );
}
