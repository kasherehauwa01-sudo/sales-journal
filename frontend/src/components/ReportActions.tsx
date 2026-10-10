import { ErrorBox } from "./States";
type Controls = {
  busy: boolean;
  loading: boolean;
  dirty: boolean;
  error: string;
  operation: string | null;
  generate: () => Promise<unknown>;
  retry: () => Promise<unknown> | undefined;
  cancel: () => void;
};
export function ReportActions({ report }: { report: Controls }) {
  return (
    <>
      <div className="analytics-build-actions">
        <button
          className="primary"
          disabled={report.busy}
          onClick={() => void report.generate()}
        >
          {report.loading ? "Формируется отчёт…" : "Сформировать отчёт"}
        </button>
        <span role="status">
          {report.operation === "export"
            ? "Формируется Excel…"
            : report.operation === "details"
              ? "Загружается детализация…"
              : report.dirty
                ? "Параметры изменены. Нажмите «Сформировать отчёт», чтобы обновить результаты."
                : "Отчёт формируется только по нажатию кнопки."}
        </span>
        {report.busy && (
          <button onClick={report.cancel}>Прекратить ожидание</button>
        )}
      </div>
      {report.error && (
        <div className="analytics-request-error" role="alert">
          <ErrorBox text={report.error} />
          <button disabled={report.busy} onClick={() => void report.retry()}>
            Повторить запрос
          </button>
        </div>
      )}
    </>
  );
}
export function ReportEmpty({ show }: { show: boolean }) {
  return show ? (
    <section className="panel analytics-empty">
      <h2>Сформируйте отчёт</h2>
      <p>Выберите параметры и нажмите «Сформировать отчёт».</p>
    </section>
  ) : null;
}
