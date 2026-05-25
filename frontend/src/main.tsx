import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/app.css";
import { applyTheme, resolveInitialTheme } from "./shared/theme/initTheme";
import { startLongTaskMonitor } from "./utils/logger";

applyTheme(resolveInitialTheme());
startLongTaskMonitor(100);

createRoot(document.getElementById("root")!).render(<App />);
