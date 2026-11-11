import {
  CheckCircle2,
  ClipboardCheck,
  FileSpreadsheet,
  History,
  RefreshCw,
  Search,
  UploadCloud,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ImportControlPanel } from "../components/ImportControlPanel";
import { ImportErrorModal } from "../components/ImportErrorModal";
import type { ImportBatch } from "../types";
import { date, datetime, number } from "../utils/format";

type View = "control" | "upload" | "journal";
const states: Record<string, string> = {
  queued: "В очереди",
  processing: "Обработка",
  completed: "Готово",
  failed: "Ошибка",
};

export function ImportsPage() {
  const [view, setView] = useState<View>("control");
  const [items, setItems] = useState<ImportBatch[]>([]),
    [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [error, setError] = useState(""),
    [selectionError, setSelectionError] = useState(""),
    [success, setSuccess] = useState(false);
  const [details, setDetails] = useState<ImportBatch>(),
    [dragging, setDragging] = useState(false);
  const [query, setQuery] = useState(""),
    [status, setStatus] = useState("all");
  const input = useRef<HTMLInputElement>(null),
    busyRef = useRef(false),
    loadingRef = useRef(false);
  const load = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    try {
      setItems(await api<ImportBatch[]>("/imports"));
      setLoaded(true);
      setListError("");
    } catch (e) {
      setListError(
        e instanceof Error ? e.message : "Не удалось загрузить журнал",
      );
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const pending = items.some((item) =>
    ["queued", "processing"].includes(item.status),
  );
  useEffect(() => {
    if (view !== "journal" && !pending) return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [view, pending, load]);
  function changeView(next: View) {
    setView(next);
    if (next === "journal") void load();
  }
  function selectFiles(selected: File[]) {
    if (busyRef.current) return;
    const accepted = selected.filter((f) => /\.(xlsx?|html?)$/i.test(f.name));
    setSelectionError(
      accepted.length !== selected.length
        ? "Поддерживаются только XLS, XLSX, HTML и HTM. Остальные файлы не добавлены."
        : "",
    );
    setFiles((current) => [...current, ...accepted]);
    setSuccess(false);
    if (input.current) input.current.value = "";
  }
  async function upload() {
    if (!files.length || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setSuccess(false);
    const body = new FormData();
    files.forEach((f) => body.append("files", f));
    try {
      await api("/imports", { method: "POST", body });
      setFiles([]);
      setSuccess(true);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось передать файлы");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function showError(item: ImportBatch) {
    try {
      setDetails(await api<ImportBatch>(`/imports/${item.id}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось открыть детали");
    }
  }
  const visible = items.filter(
    (item) =>
      (status === "all" || item.status === status) &&
      item.filename
        .toLocaleLowerCase("ru")
        .includes(query.trim().toLocaleLowerCase("ru")),
  );
  return (
    <div className="page imports-page">
      <div className="page-title imports-page-title">
        <div>
          <span className="imports-eyebrow">УПРАВЛЕНИЕ ДАННЫМИ</span>
          <h1>Импорт данных</h1>
          <p>
            Контроль поступления файлов 1С, ручная загрузка и журнал обработки.
          </p>
        </div>
        <span className="imports-format-tag">
          <FileSpreadsheet size={16} aria-hidden="true" />
          XLS · XLSX · HTML
        </span>
      </div>
      <nav
        className="imports-workspace-nav"
        aria-label="Разделы страницы импорта"
      >
        {(
          [
            { key: "control", label: "Контроль полноты", Icon: ClipboardCheck },
            { key: "upload", label: "Загрузка файлов", Icon: UploadCloud },
            { key: "journal", label: "Журнал загрузок", Icon: History },
          ] as const
        ).map(({ key, label, Icon }) => (
          <button
            type="button"
            id={`imports-tab-${key}`}
            key={key}
            aria-pressed={view === key}
            aria-controls={`imports-view-${key}`}
            onClick={() => changeView(key)}
          >
            <Icon size={18} aria-hidden="true" />
            {label}
            {key === "upload" && files.length > 0 && (
              <span className="imports-nav-count">{files.length}</span>
            )}
          </button>
        ))}
      </nav>
      {error && (
        <div className="imports-feedback imports-feedback-error" role="alert">
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setError("")}
            aria-label="Скрыть сообщение об ошибке"
          >
            <X size={16} />
          </button>
        </div>
      )}
      <section
        hidden={view !== "control"}
        id="imports-view-control"
        aria-labelledby="imports-tab-control"
      >
        <ImportControlPanel />
      </section>
      <section
        hidden={view !== "upload"}
        id="imports-view-upload"
        aria-labelledby="imports-tab-upload"
        className="imports-upload-layout"
      >
        <div className="panel imports-upload-card">
          <div className="imports-card-heading">
            <span className="imports-step-icon">
              <UploadCloud size={22} aria-hidden="true" />
            </span>
            <div>
              <h2>Загрузить файлы продаж</h2>
              <p>
                Выберите один или несколько файлов. Обработка продолжится в
                фоне.
              </p>
            </div>
          </div>
          <input
            ref={input}
            id="imports-files"
            disabled={busy}
            aria-label="Файлы продаж"
            hidden
            multiple
            type="file"
            accept=".xls,.xlsx,.html,.htm"
            onChange={(e) => selectFiles(Array.from(e.target.files || []))}
          />
          <button
            type="button"
            className={`imports-dropzone ${dragging ? "is-dragging" : ""}`}
            disabled={busy}
            onClick={() => input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              selectFiles(Array.from(e.dataTransfer.files));
            }}
            aria-label="Выбрать файлы продаж"
          >
            <UploadCloud size={32} aria-hidden="true" />
            <strong>Перетащите файлы сюда</strong>
            <span>
              или <b>выберите на устройстве</b>
            </span>
            <small>XLS, XLSX, HTML, HTM</small>
          </button>
          {selectionError && (
            <p role="alert" className="imports-inline-error">
              {selectionError}
            </p>
          )}
          {files.length > 0 && (
            <div className="imports-selected-files">
              <div className="imports-list-heading">
                <b>Выбрано файлов: {files.length}</b>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setFiles([]);
                    setSelectionError("");
                  }}
                >
                  Очистить список
                </button>
              </div>
              <ul>
                {files.map((file, i) => (
                  <li key={`${i}-${file.name}`}>
                    <FileSpreadsheet size={19} aria-hidden="true" />
                    <div>
                      <b>{file.name}</b>
                      <small>{number(file.size / 1024)} КБ</small>
                    </div>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`Убрать файл ${file.name}`}
                      onClick={() =>
                        setFiles((current) =>
                          current.filter((_, index) => index !== i),
                        )
                      }
                    >
                      <X size={17} />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="imports-upload-footer">
            <button
              type="button"
              className="primary"
              disabled={!files.length || busy}
              onClick={() => void upload()}
            >
              {busy ? (
                <RefreshCw size={18} className="spin" aria-hidden="true" />
              ) : (
                <UploadCloud size={18} aria-hidden="true" />
              )}
              {busy ? "Передаём файлы…" : "Начать импорт"}
            </button>
            <span>Выбор файлов не запускает импорт.</span>
          </div>
          {success && (
            <div
              className="imports-feedback imports-feedback-success"
              role="status"
            >
              <CheckCircle2 size={20} aria-hidden="true" />
              <div>
                <b>Файлы переданы на обработку</b>
                <p>Результат и возможные ошибки появятся в журнале загрузок.</p>
              </div>
              <button type="button" onClick={() => changeView("journal")}>
                Перейти к журналу
              </button>
            </div>
          )}
        </div>
        <aside className="panel imports-upload-help">
          <h3>Как проходит импорт</h3>
          <ol>
            <li>
              <b>Выберите файлы</b>
              <span>
                Можно добавить несколько файлов и проверить список перед
                отправкой.
              </span>
            </li>
            <li>
              <b>Запустите импорт</b>
              <span>
                Данные обрабатываются в фоне. Дождитесь результата в журнале.
              </span>
            </li>
            <li>
              <b>Проверьте результат</b>
              <span>
                Ошибка отдельной строки не останавливает обработку остальных.
                Подробности доступны по кнопке ошибок.
              </span>
            </li>
          </ol>
          <p>
            Полнота ежедневной выгрузки проверяется в разделе «Контроль
            полноты».
          </p>
          <button type="button" onClick={() => changeView("control")}>
            Перейти к контролю
          </button>
        </aside>
      </section>
      <section
        hidden={view !== "journal"}
        id="imports-view-journal"
        aria-labelledby="imports-tab-journal"
        className="panel imports-journal"
      >
        <div className="imports-journal-heading">
          <div>
            <h2>Журнал загрузок</h2>
            <p>
              Последние 200 импортов. Во время просмотра статусы обновляются
              автоматически.
            </p>
          </div>
          <button type="button" disabled={loading} onClick={() => void load()}>
            <RefreshCw
              size={16}
              className={loading ? "spin" : ""}
              aria-hidden="true"
            />
            Обновить журнал
          </button>
        </div>
        {listError && (
          <p role="alert" className="imports-inline-error">
            {listError}
          </p>
        )}
        <div className="imports-journal-filters">
          <label className="imports-journal-search">
            <span>Поиск по имени файла</span>
            <div>
              <Search size={17} aria-hidden="true" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Название файла из журнала"
              />
            </div>
          </label>
          <label>
            <span>Статус обработки</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">Все статусы</option>
              {Object.entries(states).map(([key, label]) => (
                <option value={key} key={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <span className="imports-journal-count">
            Показано: {visible.length} из {items.length}
          </span>
        </div>
        {!loaded && loading ? (
          <div className="imports-empty" role="status">
            <RefreshCw className="spin" size={25} />
            <b>Загружаем журнал…</b>
          </div>
        ) : !items.length ? (
          <div className="imports-empty">
            <History size={28} aria-hidden="true" />
            <h3>{listError ? "Журнал недоступен" : "Загрузок пока нет"}</h3>
            <p>
              {listError
                ? "Повторите загрузку журнала вручную."
                : "После отправки файлов здесь появятся результаты обработки."}
            </p>
            <button
              type="button"
              onClick={() => (listError ? void load() : changeView("upload"))}
            >
              {listError ? "Повторить загрузку журнала" : "Загрузить файлы"}
            </button>
          </div>
        ) : !visible.length ? (
          <div className="imports-empty">
            <Search size={25} aria-hidden="true" />
            <h3>По выбранным условиям ничего не найдено</h3>
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setStatus("all");
              }}
            >
              Сбросить фильтры журнала
            </button>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Файл</th>
                  <th>Загружен</th>
                  <th>Период продаж</th>
                  <th>Строк</th>
                  <th>Добавлено</th>
                  <th>Дубли</th>
                  <th>Ошибки</th>
                  <th>Статус</th>
                  <th>Время</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={item.id}>
                    <td className="imports-filename">
                      <b>{item.filename}</b>
                      <small>{number(item.file_size / 1024)} КБ</small>
                    </td>
                    <td>{datetime(item.uploaded_at)}</td>
                    <td>
                      {item.period_start
                        ? `${date(item.period_start)} — ${date(item.period_end)}`
                        : "—"}
                    </td>
                    <td>{number(item.total_rows)}</td>
                    <td>{number(item.added_rows)}</td>
                    <td>{number(item.duplicate_rows)}</td>
                    <td>
                      {item.error_rows ? (
                        <button
                          type="button"
                          className="error-count"
                          onClick={() => void showError(item)}
                        >
                          Ошибки: {number(item.error_rows)}
                        </button>
                      ) : (
                        0
                      )}
                    </td>
                    <td>
                      {item.status === "failed" ? (
                        <button
                          type="button"
                          className="status failed clickable"
                          onClick={() => void showError(item)}
                        >
                          <RefreshCw aria-hidden="true" />
                          Ошибка
                        </button>
                      ) : (
                        <span className={`status ${item.status}`}>
                          {item.status === "completed" ? (
                            <CheckCircle2 aria-hidden="true" />
                          ) : (
                            <RefreshCw aria-hidden="true" />
                          )}
                          {states[item.status] || item.status}
                        </span>
                      )}
                    </td>
                    <td>
                      {item.duration_ms
                        ? `${number(item.duration_ms / 1000)} с`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {details && (
        <ImportErrorModal
          item={details}
          onClose={() => setDetails(undefined)}
        />
      )}
    </div>
  );
}
