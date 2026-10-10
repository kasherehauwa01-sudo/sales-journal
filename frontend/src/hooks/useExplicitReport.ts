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
  ) {
    super(message);
  }
}

export async function reportResponse(
  path: string,
  signal: AbortSignal,
  init?: RequestInit,
) {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const response = await fetch(`${API}${path}`, { ...init, signal });
  if (response.status === 503)
    throw new ReportHttpError(
      "Сервер занят или недостаточно памяти. Подождите и повторите запрос вручную.",
      response.status,
    );
  if (!response.ok)
    throw new ReportHttpError(
      await response
        .json()
        .then((body) =>
          typeof body.detail === "string"
            ? body.detail
            : `Ошибка ${response.status}`,
        )
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
    retry: () => retry.current?.(),
  };
}
