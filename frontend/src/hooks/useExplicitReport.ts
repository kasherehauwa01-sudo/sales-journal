import { useRef, useState } from "react";
import { API } from "../api/client";
import {
  useAnalyticsRequest,
  type AnalyticsOperation,
} from "./useAnalyticsRequest";

export class ReportHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

export function reportErrorMessage(body: unknown, status: number): string {
  const detail = body && typeof body === "object" && "detail" in body
    ? (body as { detail: unknown }).detail : undefined;
  if (typeof detail === "string") return detail;
  if (status === 422 && Array.isArray(detail)) {
    const labels: Record<string, string> = { manager: "Менеджер", buyer_type: "Вид покупателя" };
    const legacyFilters = detail.filter(issue =>
      issue && issue.type === "string_type" && Array.isArray(issue.loc) &&
      issue.loc.length === 2 && issue.loc[0] === "body" && labels[issue.loc[1]],
    ).map(issue => labels[issue.loc[1]]);
    if (legacyFilters.length) {
      return `Работающий backend не поддерживает множественный выбор фильтров: ${[...new Set(legacyFilters)].join(", ")}. Требуется обновить backend и frontend до одной версии.`;
    }
    // Never show validation "input" values: they may contain client data.
    return "Некорректные параметры отчёта. Проверьте выбранные фильтры, товары и период.";
  }
  return `Ошибка ${status}`;
}

const PDF_ERRORS:Record<string,string>={
 memory_headroom:'Недостаточно свободной памяти для PDF: требуется не менее 512 МиБ. Повторите позже.',
 timeout:'Сервер не успел сформировать PDF за отведённое время. Повторите позже.',
 browser_unavailable:'Не удалось запустить Chromium для PDF. Требуется проверка backend.',
 browser_failed:'Chromium не смог сформировать PDF. Требуется проверка backend.',
};
const ANALYTICS_ERRORS:Record<string,string>={
 memory_headroom:'Недостаточно свободной памяти для выполнения тяжёлого запроса. Повторите позже.',
 wait_timeout:'Не удалось дождаться завершения другого тяжёлого запроса. Повторите позже.',
 queue_full:'Очередь тяжёлых запросов заполнена. Повторите позже.',
 lock_unavailable:'Защита тяжёлых операций временно недоступна. Требуется проверка backend.',
};
export function unavailableReportError(response:Response,body:unknown):ReportHttpError {
 const pdf=response.headers.get('X-Sales-PDF-Error')||'';
 if(Object.hasOwn(PDF_ERRORS,pdf)) return new ReportHttpError(PDF_ERRORS[pdf],503,`pdf_${pdf}`);
 const analytics=response.headers.get('X-Sales-Analytics-Error')||'';
 if(Object.hasOwn(ANALYTICS_ERRORS,analytics)) return new ReportHttpError(ANALYTICS_ERRORS[analytics],503,`analytics_${analytics}`);
 const detail=body&&typeof body==='object'&&'detail' in body?(body as {detail:unknown}).detail:undefined;
 // Safe compatibility with old PDF backends: do not echo arbitrary proxy HTML,
 // exception text or request values into a 503 error.
 if(detail==='Недостаточно свободной памяти для PDF. Повторите позже.')
  return new ReportHttpError(PDF_ERRORS.memory_headroom,503,'pdf_memory_headroom');
 if(detail==='Не удалось сформировать PDF за отведённое время. Повторите позже.')
  return new ReportHttpError('Backend не смог сформировать PDF: таймаут или ошибка Chromium. Требуется проверка журналов.',503,'pdf_legacy_failure');
 return new ReportHttpError('Сервер занят или недостаточно памяти. Подождите и повторите запрос вручную.',503);
}

export async function reportResponse(
  path: string,
  signal: AbortSignal,
  init?: RequestInit,
) {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const response = await fetch(`${API}${path}`, { ...init, signal });
  if (response.status === 503)
    throw unavailableReportError(response,await response.json().catch(()=>null));
  if (!response.ok)
    throw new ReportHttpError(
      await response
        .json()
        .then((body) => reportErrorMessage(body, response.status))
        .catch(() => `Ошибка ${response.status}`),
      response.status,
    );
  return response;
}
export async function reportJson<T>(
  path: string,
  signal: AbortSignal,
  init?: RequestInit,
): Promise<T> {
  return (await reportResponse(path, signal, init)).json();
}
export async function reportDownload(
  path: string,
  filename: string,
  signal: AbortSignal,
  init?: RequestInit,
) {
  const blob = await (await reportResponse(path, signal, init)).blob();
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const url = URL.createObjectURL(blob),
    link = document.createElement("a");
  try {
    link.href = url;
    link.download = filename;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

// No effect initiates a report. Each explicit action captures its parameters;
// the shared slot covers report, drill-down and Excel, including response bodies.
export function useExplicitReport<P, T>(
  draft: P,
  loader: (params: P, signal: AbortSignal) => Promise<T>,
  comparable = (params: P): unknown => params,
) {
  const request = useAnalyticsRequest();
  const [data, setData] = useState<T>();
  const [snapshot, setSnapshot] = useState<P>();
  const [error, setError] = useState("");
  const retry = useRef<(() => Promise<unknown>) | undefined>(undefined);
  async function perform<R>(
    kind: AnalyticsOperation,
    task: (signal: AbortSignal) => Promise<R>,
    success?: (value: R) => void,
  ) {
    const result = await request.run(kind, async (signal) => {
      setError("");
      retry.current = () => perform(kind, task, success);
      return task(signal);
    });
    if (result?.ok) {
      success?.(result.data);
      return result.data;
    }
    if (result) setError(result.error);
  }
  async function generate(params = draft) {
    const captured = JSON.parse(JSON.stringify(params)) as P;
    return perform(
      "report",
      (signal) => loader(captured, signal),
      (value) => {
        setData(value);
        setSnapshot(captured);
      },
    );
  }
  return {
    data,
    snapshot,
    error,
    ...request,
    loading: request.operation === "report",
    dirty:
      snapshot !== undefined &&
      JSON.stringify(comparable(draft)) !==
        JSON.stringify(comparable(snapshot)),
    generate,
    perform,
    clearError: () => setError(""),
    retry: () => retry.current?.(),
  };
}
