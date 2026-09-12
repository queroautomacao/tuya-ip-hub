// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./estilos.css";

const raiz = document.getElementById("root");
if (!raiz) throw new Error("root_ausente");

createRoot(raiz).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
