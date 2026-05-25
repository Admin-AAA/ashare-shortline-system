import { useCallback, useEffect, useState } from "react";
import { applyTheme, resolveInitialTheme, type ThemePreference } from "./initTheme";

export function useThemePreference() {
  const [theme, setTheme] = useState<ThemePreference>(() => resolveInitialTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return { theme, setTheme, toggleTheme };
}
