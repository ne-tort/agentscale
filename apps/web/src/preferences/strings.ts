import type { Locale } from "./settings";

/** Every user-visible string, per locale. Typed so a missing key fails the build. */
export type Strings = {
  signIn: string;
  signingIn: string;
  login: string;
  password: string;
  loginHint: string;
  settings: string;
  language: string;
  languageRu: string;
  languageEn: string;
  theme: string;
  themeSystem: string;
  themeLight: string;
  themeDark: string;
  close: string;
  showPassword: string;
  hidePassword: string;
  invalidCredentials: string;
  signInFailed: string;
};

export const STRINGS: Record<Locale, Strings> = {
  ru: {
    signIn: "Вход",
    signingIn: "Входим…",
    login: "Логин",
    password: "Пароль",
    loginHint: "company@agentscale.local",
    settings: "Настройки",
    language: "Язык",
    languageRu: "Русский",
    languageEn: "English",
    theme: "Тема",
    themeSystem: "Системная",
    themeLight: "Светлая",
    themeDark: "Тёмная",
    close: "Закрыть",
    showPassword: "Показать пароль",
    hidePassword: "Скрыть пароль",
    invalidCredentials: "Неверный логин или пароль",
    signInFailed: "Не удалось войти",
  },
  en: {
    signIn: "Sign in",
    signingIn: "Signing in…",
    login: "Login",
    password: "Password",
    loginHint: "company@agentscale.local",
    settings: "Settings",
    language: "Language",
    languageRu: "Русский",
    languageEn: "English",
    theme: "Theme",
    themeSystem: "System",
    themeLight: "Light",
    themeDark: "Dark",
    close: "Close",
    showPassword: "Show password",
    hidePassword: "Hide password",
    invalidCredentials: "Invalid login or password",
    signInFailed: "Unable to sign in",
  },
};
