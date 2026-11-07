import { defineStrings } from "../core";

/** The company's own area. */
export default defineStrings({
  ru: {
    adminHasNoCompany: "Администратор платформы не привязан к компании. Компаниями управляют в разделе «Администрирование».",
    noCompanyScope: "Для этой учётной записи не указана компания.",
  },
  en: {
    adminHasNoCompany: "A platform administrator has no company of their own. Companies are managed under Administration.",
    noCompanyScope: "This account has no company assigned.",
  },
});
