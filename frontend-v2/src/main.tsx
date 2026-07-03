import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { applyTheme } from "./shared/theme/initTheme";
import { App } from "./App";

applyTheme("dark");

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
