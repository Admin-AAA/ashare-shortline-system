import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { applyTheme, resolveInitialTheme } from "./shared/theme/initTheme";
import { App } from "./App";
import { ServerStateProvider } from "./app/ServerStateProvider";
import "./styles/app.css";

applyTheme(resolveInitialTheme()); // 记住用户的深/浅色选择，默认深色

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <ServerStateProvider>
      <App />
    </ServerStateProvider>
  </StrictMode>
);
