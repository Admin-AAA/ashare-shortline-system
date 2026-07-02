export type ThemePreference = "dark" | "light";

export const THEME_STORAGE_KEY = "ashare-theme";

export function resolveInitialTheme(): ThemePreference {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return "light";
}

export function applyTheme(theme: ThemePreference) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}
