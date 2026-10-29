import { expect, it } from "vitest";
import { reportErrorMessage } from "../src/hooks/useExplicitReport";
it("explains an old backend rejecting array filters without exposing validation input", () => {
  const message = reportErrorMessage({ detail: [
    { loc: ["body", "manager"], type: "string_type", input: ["Private manager"] },
    { loc: ["body", "buyer_type"], type: "string_type", input: ["Розница"] },
  ] }, 422);
  expect(message).toContain("backend не поддерживает множественный выбор");
  expect(message).toContain("Менеджер, Вид покупателя");
  expect(message).toContain("обновить backend и frontend до одной версии");
  expect(message).not.toContain("Private manager");
  expect(message).not.toContain("Розница");
});
it("does not misidentify invalid values in the current union schema as an old backend", () => {
  expect(reportErrorMessage({ detail: [
    { loc: ["body", "manager", "str"], type: "string_type", input: 17 },
    { loc: ["body", "manager", "list[str]"], type: "list_type", input: 17 },
  ] }, 422)).toContain("Некорректные параметры отчёта");
});
it("retains server messages and actual HTTP status codes", () => {
  expect(reportErrorMessage({ detail: "Источник недоступен" }, 502)).toBe("Источник недоступен");
  expect(reportErrorMessage({}, 500)).toBe("Ошибка 500");
  expect(reportErrorMessage(null, 422)).toBe("Ошибка 422");
});
