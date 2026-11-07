import { defineStrings } from "../core";

/** Sign-in screen and the settings popover it opens. */
export default defineStrings({
  ru: {
    title: "Вход",
    signingIn: "Входим…",
    login: "Логин",
    password: "Пароль",
    loginHint: "company@agentscale.local",
    invalidCredentials: "Неверный логин или пароль",
    failed: "Не удалось войти",
    openSettings: "Настройки",
  },
  en: {
    title: "Sign in",
    signingIn: "Signing in…",
    login: "Login",
    password: "Password",
    loginHint: "company@agentscale.local",
    invalidCredentials: "Invalid login or password",
    failed: "Unable to sign in",
    openSettings: "Settings",
  },
});
