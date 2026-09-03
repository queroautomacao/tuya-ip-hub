// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: an address changes when the router hands the device another lease, and without
// this screen correcting it costs the stored credential and a whole re registration.
// Por que: um endereço muda quando o roteador dá outra concessão ao aparelho, e sem esta
// tela corrigi-lo custa a credencial guardada e um cadastro inteiro de novo.

import { useState, type FormEvent } from "react";
import FormularioEquipamento, { rotuloDoCampo } from "./FormularioEquipamento.tsx";
import { atualizarEquipamento, codigoDoErro } from "./api.ts";
import type { Equipamento, ItemCatalogo } from "./equipamentos.ts";
import { formularioDe, validarCadastro, type Formulario } from "./formulario.ts";
import { t, traduzirErro, type Idioma } from "./i18n";

export default function EditarEquipamento({
  equipamento,
  item,
  idioma,
  aoSalvar,
  aoCancelar,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  aoSalvar: () => void;
  aoCancelar: () => void;
}) {
  const [formulario, setFormulario] = useState<Formulario>(() => formularioDe(equipamento, item));
  const [erro, setErro] = useState<{ codigo: string; campo: string } | null>(null);
  const [enviando, setEnviando] = useState(false);
  const guardados = equipamento.segredos_definidos;

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    const validacao = validarCadastro(formulario, item === undefined ? [] : [item], guardados);
    if (!validacao.ok) {
      setErro({ codigo: validacao.codigo, campo: validacao.campo });
      return;
    }
    setErro(null);
    setEnviando(true);
    try {
      await atualizarEquipamento(equipamento.identidade, validacao.corpo);
      aoSalvar();
    } catch (falha) {
      setErro({ codigo: codigoDoErro(falha), campo: "" });
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
      <h4>{t("editar_titulo")}</h4>
      <FormularioEquipamento
        item={item}
        idioma={idioma}
        formulario={formulario}
        guardados={guardados}
        prefixo={`editar-${equipamento.identidade}`}
        fixarIdentidade
        aoMudar={setFormulario}
      />
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro.codigo)}
          {erro.campo && ` (${rotuloDoCampo(item, idioma, erro.campo)})`}
        </p>
      )}
      <div className="acoes">
        <button type="submit" className="botao" disabled={enviando}>
          {enviando ? t("enviando") : t("editar_salvar")}
        </button>
        <button type="button" className="botao secundario" onClick={aoCancelar}>
          {t("editar_cancelar")}
        </button>
      </div>
    </form>
  );
}
