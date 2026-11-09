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

it('keeps safe PDF 503 reasons without reflecting unknown server content',async()=>{
 const {unavailableReportError}=await import('../src/hooks/useExplicitReport');
 for(const [code,message] of [['memory_headroom','512'],['timeout','не успел'],['browser_unavailable','запустить Chromium'],['browser_failed','Chromium не смог']]) {
  const error=unavailableReportError(new Response('',{status:503,headers:{'X-Sales-PDF-Error':code}}),{detail:'PRIVATE CLIENT TOKEN'});
  expect(error.message).toContain(message);expect(error.code).toBe(`pdf_${code}`);
  expect(error.message).not.toContain('PRIVATE');
 }
 const unknown=unavailableReportError(new Response('',{status:503,headers:{'X-Sales-PDF-Error':'PRIVATE TOKEN'}}),{detail:'PRIVATE CLIENT TOKEN'});
 expect(unknown.message).not.toContain('PRIVATE');
 expect(unknown.code).toBeUndefined();
});
it('distinguishes analytics admission errors from PDF rendering errors',async()=>{
 const {unavailableReportError}=await import('../src/hooks/useExplicitReport');
 for(const code of ['memory_headroom','wait_timeout','queue_full','lock_unavailable']) {
  expect(unavailableReportError(new Response('',{status:503,headers:{'X-Sales-Analytics-Error':code}}),{}).code).toBe(`analytics_${code}`);
 }
});
it('recognizes an old PDF memory response without guessing old Chromium failures are memory',async()=>{
 const {unavailableReportError}=await import('../src/hooks/useExplicitReport');
 const response=new Response('',{status:503});
 expect(unavailableReportError(response,{detail:'Недостаточно свободной памяти для PDF. Повторите позже.'}).code).toBe('pdf_memory_headroom');
 expect(unavailableReportError(response,{detail:'Не удалось сформировать PDF за отведённое время. Повторите позже.'}).code).toBe('pdf_legacy_failure');
});
