// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a licence is a device on the platform (section 8): an identity the bridge uses, a QR
// code the customer scans on the app, and a slice of the data points the registered equipment
// occupy by number. The owner sees, creates, edits and removes them here, on the account,
// because a licence is a fact about the installation and not about one equipment. The chave is
// written and never read back (section 9); the QR carries only the pid and the uuid.
// Por que: uma licença é um dispositivo na plataforma (seção 8): uma identidade que a ponte usa,
// um QR code que o cliente escaneia no app, e uma fatia dos data points que os equipamentos
// cadastrados ocupam por número. O dono vê, cria, edita e remove aqui, na conta, porque uma
// licença é um fato sobre a instalação e não sobre um equipamento. A chave é escrita e nunca
// lida de volta (seção 9); o QR leva só o pid e o uuid.

import QRCode from "qrcode";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  atualizarLicenca,
  codigoDoErro,
  criarLicenca,
  lerLicencas,
  lerQrDaLicenca,
  removerLicenca,
} from "./api.ts";
import { INTERVALO_MS, PRODUTOS, type Produto } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";
import { idValido, type CorpoDeLicenca, type LeituraDeLicencas, type Licenca } from "./licencas.ts";

const NOME_MAXIMO = 40;
const IDENTIFICADOR_MAXIMO = 64;
const CHAVE_MAXIMA = 128;
const LARGURA_DO_QR = 220;

interface Rascunho {
  id: string;
  produto: Produto;
  nome: string;
  uuid: string;
  pid: string;
  chave: string;
}

const NOVO: Rascunho = { id: "", produto: "av", nome: "", uuid: "", pid: "", chave: "" };

function rascunhoDe(licenca: Licenca): Rascunho {
  // Why: the chave never comes back from the daemon, so the edit form starts it blank and a
  // blank chave on save means "keep the stored one".
  // Por que: a chave nunca volta do daemon, então o formulário de edição a começa em branco e
  // uma chave em branco ao salvar significa "mantenha a guardada".
  return { id: licenca.id, produto: licenca.produto, nome: licenca.nome, uuid: licenca.uuid, pid: licenca.pid, chave: "" };
}

function corpoDe(rascunho: Rascunho, anterior: Licenca | null): CorpoDeLicenca {
  const corpo: CorpoDeLicenca = {
    nome: rascunho.nome.trim(),
    uuid: rascunho.uuid.trim(),
    pid: rascunho.pid.trim(),
  };
  if (anterior === null) {
    corpo.produto = rascunho.produto;
    if (rascunho.id.trim() !== "") corpo.id = rascunho.id.trim();
  }
  if (rascunho.chave.trim() !== "" || anterior === null) corpo.chave = rascunho.chave.trim();
  return corpo;
}

function Formulario({
  anterior,
  ocupado,
  aoSalvar,
  aoCancelar,
}: {
  anterior: Licenca | null;
  ocupado: boolean;
  aoSalvar: (corpo: CorpoDeLicenca) => void;
  aoCancelar: () => void;
}) {
  const [rascunho, setRascunho] = useState<Rascunho>(anterior === null ? NOVO : rascunhoDe(anterior));
  const idOk = idValido(rascunho.id.trim());
  const mudar = (campo: keyof Rascunho, valor: string): void => setRascunho({ ...rascunho, [campo]: valor });
  function enviar(evento: FormEvent<HTMLFormElement>): void {
    evento.preventDefault();
    if (!idOk) return;
    aoSalvar(corpoDe(rascunho, anterior));
  }
  return (
    <form className="formulario licenca-formulario" onSubmit={enviar}>
      {anterior === null && (
        <>
          <label htmlFor="licenca-produto">{t("licencas_produto")}</label>
          <select
            id="licenca-produto"
            value={rascunho.produto}
            onChange={(evento) => mudar("produto", evento.target.value)}
          >
            {PRODUTOS.map((produto) => (
              <option key={produto} value={produto}>
                {t(`produto_${produto}` as const)}
              </option>
            ))}
          </select>
          <label htmlFor="licenca-id">{t("licencas_id")}</label>
          <input
            id="licenca-id"
            type="text"
            maxLength={40}
            autoComplete="off"
            value={rascunho.id}
            aria-invalid={!idOk}
            onChange={(evento) => mudar("id", evento.target.value)}
          />
          <p className="dica">{t("licencas_id_dica")}</p>
        </>
      )}
      <label htmlFor="licenca-nome">{t("licencas_nome")}</label>
      <input
        id="licenca-nome"
        type="text"
        maxLength={NOME_MAXIMO}
        autoComplete="off"
        value={rascunho.nome}
        onChange={(evento) => mudar("nome", evento.target.value)}
      />
      <label htmlFor="licenca-uuid">{t("licencas_uuid")}</label>
      <input
        id="licenca-uuid"
        type="text"
        maxLength={IDENTIFICADOR_MAXIMO}
        autoComplete="off"
        spellCheck={false}
        value={rascunho.uuid}
        onChange={(evento) => mudar("uuid", evento.target.value)}
      />
      <label htmlFor="licenca-pid">{t("licencas_pid")}</label>
      <input
        id="licenca-pid"
        type="text"
        maxLength={IDENTIFICADOR_MAXIMO}
        autoComplete="off"
        spellCheck={false}
        value={rascunho.pid}
        onChange={(evento) => mudar("pid", evento.target.value)}
      />
      <label htmlFor="licenca-chave">{t("licencas_chave")}</label>
      <input
        id="licenca-chave"
        type="password"
        maxLength={CHAVE_MAXIMA}
        autoComplete="off"
        value={rascunho.chave}
        onChange={(evento) => mudar("chave", evento.target.value)}
      />
      <p className="dica">{t("licencas_chave_dica")}</p>
      <div className="acoes-largas">
        <button type="submit" className="botao" disabled={ocupado || !idOk}>
          {ocupado ? t("enviando") : anterior === null ? t("licencas_criar") : t("licencas_salvar")}
        </button>
        <button type="button" className="botao secundario" disabled={ocupado} onClick={aoCancelar}>
          {t("licencas_cancelar")}
        </button>
      </div>
    </form>
  );
}

function Qr({ id, aoFechar }: { id: string; aoFechar: () => void }) {
  const tela = useRef<HTMLCanvasElement | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [conteudo, setConteudo] = useState<string | null>(null);
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const qr = await lerQrDaLicenca(id);
        if (!vivo) return;
        setConteudo(qr.conteudo);
        setErro(null);
      } catch (falha) {
        if (vivo) setErro(codigoDoErro(falha));
      }
    })();
    return () => {
      vivo = false;
    };
  }, [id]);
  // Why: the canvas is unmounted while an error shows and mounted again empty when the error
  // clears, so the drawing follows the error too and never leaves a blank square behind.
  // Por que: o canvas é desmontado enquanto um erro aparece e montado de novo vazio quando o
  // erro some, então o desenho segue o erro também e nunca deixa um quadrado em branco.
  useEffect(() => {
    if (conteudo === null || erro !== null || tela.current === null) return;
    void QRCode.toCanvas(tela.current, conteudo, { width: LARGURA_DO_QR, margin: 1 }).catch(() =>
      setErro("erro_http"),
    );
  }, [conteudo, erro]);
  return (
    <div className="qr-caixa" role="dialog" aria-label={t("licencas_qr")}>
      <h3>{t("licencas_qr")}</h3>
      {erro !== null ? (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      ) : (
        <canvas ref={tela} className="qr-tela" width={LARGURA_DO_QR} height={LARGURA_DO_QR} />
      )}
      <p className="dica">{t("licencas_qr_ajuda")}</p>
      <button type="button" className="botao secundario" onClick={aoFechar}>
        {t("licencas_qr_fechar")}
      </button>
    </div>
  );
}

function Cartao({
  licenca,
  ocupado,
  aoEditar,
  aoRemover,
  aoMostrarQr,
}: {
  licenca: Licenca;
  ocupado: boolean;
  aoEditar: () => void;
  aoRemover: () => void;
  aoMostrarQr: () => void;
}) {
  const [confirmando, setConfirmando] = useState(false);
  const ocupados = licenca.numeros.filter((numero) => numero.identidade !== "").length;
  return (
    <li className="licenca-item">
      <div className="licenca-cabeca">
        <div>
          <h3>{licenca.nome || licenca.id}</h3>
          <p className="texto-suave">
            {t(`produto_${licenca.produto}` as const)}
            {" · "}
            <code>{licenca.id}</code>
          </p>
        </div>
        <span className={`etiqueta ${licenca.chave_definida ? "" : "etiqueta-aviso"}`}>
          {licenca.chave_definida ? t("licencas_chave_definida") : t("licencas_chave_ausente")}
        </span>
      </div>
      <dl className="licenca-dados">
        <dt>{t("licencas_uuid")}</dt>
        <dd>{licenca.uuid || "..."}</dd>
        <dt>{t("licencas_pid")}</dt>
        <dd>{licenca.pid || "..."}</dd>
        <dt>{t("licencas_numeros")}</dt>
        <dd>{`${ocupados} ${t("licencas_de")} ${licenca.capacidade}`}</dd>
        <dt>{t("licencas_reports")}</dt>
        <dd>{licenca.reports_do_dia}</dd>
        <dt>{t("licencas_ouvintes")}</dt>
        <dd>{licenca.ouvintes}</dd>
      </dl>
      {confirmando ? (
        <div className="confirmacao">
          <p>{t("licencas_remover_pergunta")}</p>
          <button type="button" className="botao perigo" disabled={ocupado} onClick={aoRemover}>
            {t("licencas_remover_confirmar")}
          </button>
          <button type="button" className="botao secundario" onClick={() => setConfirmando(false)}>
            {t("licencas_cancelar")}
          </button>
        </div>
      ) : (
        <div className="acoes-largas">
          <button type="button" className="botao" disabled={ocupado} onClick={aoMostrarQr}>
            {t("licencas_qr")}
          </button>
          <button type="button" className="botao secundario" disabled={ocupado} onClick={aoEditar}>
            {t("licencas_editar")}
          </button>
          <button type="button" className="botao secundario perigo-suave" disabled={ocupado} onClick={() => setConfirmando(true)}>
            {t("licencas_remover")}
          </button>
        </div>
      )}
    </li>
  );
}

export default function Licencas() {
  const [leitura, setLeitura] = useState<LeituraDeLicencas | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [editando, setEditando] = useState<"nova" | string | null>(null);
  const [qrDe, setQrDe] = useState<string | null>(null);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      setLeitura(await lerLicencas());
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }, []);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  async function chamar(trabalho: () => Promise<void>, feito: string): Promise<void> {
    setOcupado(true);
    setAviso(null);
    try {
      await trabalho();
      setErro(null);
      setAviso(feito);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  const licencas = leitura?.licencas ?? [];
  const emEdicao = licencas.find((licenca) => licenca.id === editando) ?? null;
  return (
    <section className="cartao">
      <h2>{t("licencas_titulo")}</h2>
      <p className="texto-suave">{t("licencas_intro")}</p>
      {leitura !== null && licencas.length === 0 && editando === null && (
        <p className="dica">{t("licencas_vazio")}</p>
      )}
      {licencas.length > 0 && (
        <ul className="licenca-lista">
          {licencas.map((licenca) =>
            editando === licenca.id ? (
              <li key={licenca.id} className="licenca-item">
                <Formulario
                  anterior={licenca}
                  ocupado={ocupado}
                  aoSalvar={(corpo) =>
                    void chamar(async () => {
                      await atualizarLicenca(licenca.id, corpo);
                      setEditando(null);
                    }, t("licencas_salva"))
                  }
                  aoCancelar={() => setEditando(null)}
                />
              </li>
            ) : (
              <Cartao
                key={licenca.id}
                licenca={licenca}
                ocupado={ocupado}
                aoEditar={() => setEditando(licenca.id)}
                aoRemover={() =>
                  void chamar(async () => {
                    await removerLicenca(licenca.id);
                    if (qrDe === licenca.id) setQrDe(null);
                  }, t("licencas_removida"))
                }
                aoMostrarQr={() => setQrDe(qrDe === licenca.id ? null : licenca.id)}
              />
            ),
          )}
        </ul>
      )}
      {qrDe !== null && licencas.some((licenca) => licenca.id === qrDe) && (
        <Qr key={qrDe} id={qrDe} aoFechar={() => setQrDe(null)} />
      )}
      {editando === "nova" ? (
        <Formulario
          anterior={null}
          ocupado={ocupado}
          aoSalvar={(corpo) =>
            void chamar(async () => {
              await criarLicenca(corpo);
              setEditando(null);
            }, t("licencas_criada"))
          }
          aoCancelar={() => setEditando(null)}
        />
      ) : (
        emEdicao === null && (
          <button type="button" className="botao" disabled={ocupado} onClick={() => setEditando("nova")}>
            + {t("licencas_nova")}
          </button>
        )
      )}
      {aviso !== null && (
        <p className="sucesso" role="status">
          {aviso}
        </p>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
    </section>
  );
}
