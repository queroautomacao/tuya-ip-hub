// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a driver file is code the integrator reads line by line, and a plain textarea gives no
// line to point at when the daemon refuses "comandos.fonte.valores". This is a textarea with a
// gutter of numbers that scrolls with it and a Tab that indents, and nothing more: no
// highlighting library, because a library is a dependency the image would carry (section 10).
// Por que: um arquivo de driver é código que o integrador lê linha a linha, e um textarea puro
// não dá linha para apontar quando o daemon recusa "comandos.fonte.valores". Isto é um
// textarea com uma calha de números que rola junto e um Tab que indenta, e nada mais: sem
// biblioteca de realce, porque biblioteca é dependência que a imagem carregaria (seção 10).

import { useLayoutEffect, useRef, type KeyboardEvent, type UIEvent } from "react";

const RECUO = "  ";
const LINHAS_MINIMAS = 16;

export function numerosDe(valor: string): string {
  const total = Math.max(LINHAS_MINIMAS, valor.split("\n").length);
  return Array.from({ length: total }, (_ignorado, indice) => String(indice + 1)).join("\n");
}

export function comRecuo(valor: string, inicio: number, fim: number): { texto: string; cursor: number } {
  return { texto: valor.slice(0, inicio) + RECUO + valor.slice(fim), cursor: inicio + RECUO.length };
}

export default function EditorDeCodigo({
  id,
  nome,
  valor,
  aoMudar,
}: {
  id: string;
  nome: string;
  valor: string;
  aoMudar: (novo: string) => void;
}) {
  const calha = useRef<HTMLPreElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const cursor = useRef<number | null>(null);

  // Why: React re-renders the textarea with the new text before the caret can be placed, so
  // the position is kept until the layout is done and set then, or Tab would jump to the end.
  // Por que: o React redesenha o textarea com o texto novo antes de o cursor poder ser posto,
  // então a posição é guardada até o layout terminar e é posta aí, senão Tab pularia ao fim.
  useLayoutEffect(() => {
    if (cursor.current !== null && area.current !== null) {
      area.current.setSelectionRange(cursor.current, cursor.current);
      cursor.current = null;
    }
  });

  function aoTeclar(evento: KeyboardEvent<HTMLTextAreaElement>): void {
    if (evento.key !== "Tab") return;
    evento.preventDefault();
    const { selectionStart, selectionEnd } = evento.currentTarget;
    const novo = comRecuo(valor, selectionStart, selectionEnd);
    cursor.current = novo.cursor;
    aoMudar(novo.texto);
  }

  function aoRolar(evento: UIEvent<HTMLTextAreaElement>): void {
    if (calha.current !== null) calha.current.scrollTop = evento.currentTarget.scrollTop;
  }

  return (
    <div className="editor">
      <pre className="editor-numeros" aria-hidden="true" ref={calha}>
        {numerosDe(valor)}
      </pre>
      <textarea
        id={id}
        ref={area}
        className="editor-json"
        name={nome}
        rows={LINHAS_MINIMAS}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        wrap="off"
        value={valor}
        onChange={(evento) => aoMudar(evento.target.value)}
        onKeyDown={aoTeclar}
        onScroll={aoRolar}
      />
    </div>
  );
}
