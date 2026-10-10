import { useEffect, useRef, useState } from "react";

export type AnalyticsOperation = "report" | "details" | "export";
export const cancelledMessage =
  "Ожидание отменено. Расчёт на сервере может продолжаться. Повторите запрос позже вручную.";

// One page-level slot also covers reading JSON/blob and preparing the download.
// It is acquired synchronously, before React has time to render disabled buttons.
export function useAnalyticsRequest() {
  const pending = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [operation, setOperation] = useState<AnalyticsOperation | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      pending.current?.abort();
    };
  }, []);

  async function run<T>(
    kind: AnalyticsOperation,
    task: (signal: AbortSignal) => Promise<T>,
  ) {
    if (pending.current || !mounted.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setOperation(kind);
    try {
      const data = await task(controller.signal);
      // A browser cancellation does not establish that backend work stopped.
      if (controller.signal.aborted)
        throw new DOMException("Aborted", "AbortError");
      if (mounted.current) return { ok: true as const, data };
    } catch (error) {
      if (mounted.current)
        return {
          ok: false as const,
          error:
            controller.signal.aborted ||
            (error instanceof Error && error.name === "AbortError")
              ? cancelledMessage
              : error instanceof TypeError
                ? "Не удалось связаться с сервером. Проверьте соединение и повторите запрос вручную."
                : error instanceof Error
                  ? error.message
                  : "Не удалось выполнить запрос. Повторите позже вручную.",
        };
    } finally {
      pending.current = null;
      if (mounted.current) setOperation(null);
    }
  }
  return {
    run,
    operation,
    busy: operation !== null,
    cancel: () => pending.current?.abort(),
  };
}
