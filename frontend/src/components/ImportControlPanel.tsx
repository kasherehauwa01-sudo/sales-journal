import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { date, datetime } from "../utils/format";

export function missingFileLabel(n: number) {
  if (n % 10 === 1 && n % 100 !== 11) return `Не загружен ${n} файл`;
  return `Не загружено ${n} ${n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "файла" : "файлов"}`;
}
const labels: Record<string, string> = {
  waiting: "Ожидание",
  complete: "Полный импорт",
  incomplete: "Неполный импорт",
  error: "Ошибка импорта",
  unavailable: "Проверка недоступна",
  missing: "Не получен",
  resolved: "Устранено",
  closed: "Закрыто администратором",
  success: "Успешно импортирован",
  queued: "В очереди",
  processing: "Обработка",
  pending: "Ожидает отправки",
  sending: "Отправляется",
  sent: "Отправлено",
  failed: "Ошибка",
  uncertain: "Доставка не подтверждена",
  cancelled: "Отменено",
  finished: "Завершён",
  running: "Работает",
  interrupted: "Прерван",
};
const reasons: Record<string, string> = {
  unverified_period: "Период файла не подтверждён",
  control_not_enabled:
    "Контроль не включён или дата раньше утверждённого начала",
  no_rules: "Нет действующих правил подразделений",
  cycle_not_checked: "Последний цикл ещё не прошёл проверку",
  ftp_listing_failed: "Не удалось получить список файлов FTP",
  cycle_interrupted: "Цикл был прерван",
  worker_interrupted: "Предыдущий процесс был прерван",
  empty_file: "Файл имеет нулевой размер",
  import_failed: "Ошибка обработки файла",
  validation_failed: "Строки отклонены действующим валидатором",
  ftp_download_failed: "Ошибка получения файла FTP",
  corrected_source_requires_review:
    "Исправленная версия требует проверки переимпорта",
  legacy_source_requires_review:
    "Для прежнего файла нет контрольной суммы; нужна проверка",
  source_period_requires_review:
    "Не удалось подтвердить новый период изменённого файла",
  evidence_limit: "Превышен безопасный объём метаданных",
  legacy_evidence_limit: "Слишком много прежних попыток без сведений о периоде",
};
function Modal({
  title,
  close,
  children,
  focusTarget,
}: {
  title: string;
  focusTarget?: HTMLElement | null;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const node = ref.current!,
      previous = focusTarget || (document.activeElement as HTMLElement | null);
    if (node.showModal) node.showModal();
    else node.setAttribute("open", "");
    node.querySelector<HTMLElement>("button,input,select,textarea")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
      if (event.key === "Tab") {
        const nodes = [
          ...node.querySelectorAll<HTMLElement>(
            'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
          ),
        ];
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    node.addEventListener("keydown", key);
    return () => {
      node.removeEventListener("keydown", key);
      if (node.close) node.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="import-control-dialog"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <div className="section-title">
        <h2>{title}</h2>
        <button onClick={close} aria-label="Закрыть окно">
          Закрыть
        </button>
      </div>
      {children}
    </dialog>
  );
}
type Rule = {
  id: number;
  start_date: string;
  end_date: string | null;
  filename_template: string;
  date_format: string;
  display_name: string;
  active: boolean;
  controlled: boolean;
};
type Department = { id: number; code: string; name: string; rules: Rule[] };
type Config = {
  enabled: boolean;
  automatic: boolean;
  email_enabled: boolean;
  start_date: string | null;
  day_offset: number | null;
  marker_template: string | null;
  recipients: string;
  revision: number;
};
type Issue = {
  id: number;
  sales_date: string;
  department_name: string;
  department_id: number;
  expected_filename: string;
  status: string;
  error_code: string | null;
  checked_at: string;
  closure_reason: string | null;
};
type Page<T> = {
  items: T[];
  total: number;
  page: number;
  last_checked_at?: string;
};
const today = () => new Date().toLocaleDateString("en-CA");
export function ImportControlPanel() {
  const [state, setState] = useState<any>(),
    [config, setConfig] = useState<Config>(),
    [departments, setDepartments] = useState<Department[]>([]),
    [tab, setTab] = useState("control"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [from, setFrom] = useState(today),
    [to, setTo] = useState(today),
    [department, setDepartment] = useState(""),
    [status, setStatus] = useState("open"),
    [direction, setDirection] = useState("desc"),
    [page, setPage] = useState(1),
    [issues, setIssues] = useState<Page<Issue>>(),
    [modal, setModal] = useState(""),
    [details, setDetails] = useState<any>(),
    [audit, setAudit] = useState<any>(),
    [notices, setNotices] = useState<any[]>([]),
    [candidates, setCandidates] = useState<any[]>();
  const busyRef = useRef(false);
  const focusReturn = useRef<HTMLElement | null>(null);
  const [noticePage, setNoticePage] = useState(1),
    [noticeTotal, setNoticeTotal] = useState(0),
    [auditDepartment, setAuditDepartment] = useState<number>();
  const [form, setForm] = useState({
      id: "",
      code: "",
      name: "",
      start_date: today(),
      end_date: "",
      filename_template: "",
      date_format: "dmy",
      active: true,
      controlled: true,
    }),
    [approve, setApprove] = useState(false),
    [reason, setReason] = useState("");
  async function load() {
    const [s, c, d] = await Promise.all([
      api("/import-control/status"),
      api<Config>("/import-control/settings"),
      api<Department[]>("/import-control/departments"),
    ]);
    setState(s);
    setConfig(c);
    setDepartments(d);
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  async function action(task: () => Promise<unknown>) {
    if (busyRef.current) return;
    focusReturn.current = document.activeElement as HTMLElement;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить действие",
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function loadIssues(p = page) {
    const query = new URLSearchParams({ page: String(p), status, direction });
    if (from) query.set("date_from", from);
    if (to) query.set("date_to", to);
    if (department) query.set("department_id", department);
    setIssues(await api<Page<Issue>>(`/import-control/issues?${query}`));
    setPage(p);
  }
  async function openIssues() {
    setFrom("");
    setTo("");
    setDepartment("");
    setStatus("open");
    setDirection("desc");
    setDetails(undefined);
    setPage(1);
    setModal("issues");
    setIssues(
      await api<Page<Issue>>("/import-control/issues?status=open&page=1"),
    );
  }
  async function refresh() {
    if (!from && !to) {
      await api("/import-control/check-open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } else {
      if (!from || !to)
        throw new Error("Выберите обе границы периода (не более 31 дня)");
      await api("/import-control/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: from, date_to: to }),
      });
    }
    await load();
    if (modal === "issues") await loadIssues();
  }
  async function saveDepartment() {
    const payload = {
      name: form.name,
      start_date: form.start_date,
      end_date: form.end_date || null,
      filename_template: form.filename_template,
      date_format: form.date_format,
      active: form.active,
      controlled: form.controlled,
    };
    await api(
      form.id
        ? `/import-control/departments/${form.id}/rules`
        : "/import-control/departments",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          form.id ? payload : { ...payload, code: form.code },
        ),
      },
    );
    await load();
    setModal("");
  }
  function edit(row?: Department) {
    focusReturn.current = document.activeElement as HTMLElement;
    const r = row?.rules[row.rules.length - 1];
    setForm({
      id: row ? String(row.id) : "",
      code: row?.code || "",
      name: row?.name || "",
      start_date: today(),
      end_date: r?.end_date || "",
      filename_template: r?.filename_template || "",
      date_format: r?.date_format || "dmy",
      active: r?.active ?? true,
      controlled: r?.controlled ?? true,
    });
    setModal("department");
  }
  async function closeIssue(row: Issue) {
    if (reason.trim().length < 5)
      throw new Error("Укажите причину закрытия (не менее 5 символов)");
    await api(`/import-control/issues/${row.id}/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
    await loadIssues();
    await load();
    setReason("");
  }
  async function loadNotices(p = 1) {
    const result = await api<any>(`/import-control/notices?page=${p}`);
    setNotices(result.items);
    setNoticePage(p);
    setNoticeTotal(result.total);
  }
  async function loadAudit(id: number, p = 1) {
    setAuditDepartment(id);
    setAudit(await api(`/import-control/audit?department_id=${id}&page=${p}`));
    setModal("audit");
  }
  const latest = error ? undefined : state?.latest,
    missing = state?.open_counts?.missing || 0,
    errors = state?.open_counts?.error || 0;
  return (
    <section
      className="panel import-control-panel"
      aria-label="Контроль полноты импорта"
    >
      <div className="section-title">
        <h2>Контроль импорта</h2>
        <button disabled={busy} onClick={() => void action(load)}>
          Обновить статус
        </button>
      </div>
      <div className="tabs" aria-label="Разделы контроля">
        {[
          ["control", "Контроль"],
          ["departments", "Подразделения"],
          ["history", "История пропусков"],
          ["notices", "Уведомления"],
          ["settings", "Настройки контроля"],
        ].map(([key, label]) => (
          <button
            key={key}
            aria-pressed={tab === key}
            onClick={() => {
              setTab(key);
              if (key === "history") void action(openIssues);
              if (key === "notices") void action(() => loadNotices());
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {busy && <p role="status">Выполняется…</p>}
      {!config && !error && <p>Загрузка контроля…</p>}
      {(missing > 0 || errors > 0) && (
        <div className="report-warning">
          <button disabled={busy} onClick={() => void action(openIssues)}>
            {missing > 0
              ? missingFileLabel(missing)
              : "Файлы получены, но не импортированы"}
            {errors > 0 ? ` · Ошибок: ${errors}` : ""}
          </button>
        </div>
      )}
      {tab === "control" && (
        <>
          <p>
            <b>
              {error
                ? "Проверка недоступна"
                : !state?.enabled
                  ? "Контроль отключён"
                  : labels[latest?.status] || "Проверка ещё не выполнена"}
            </b>
            {latest && ` · Дата продаж: ${date(latest.sales_date)}`}
          </p>
          <div className="field-grid">
            {[
              ["Ожидается", latest?.expected],
              ["Успешно импортировано", latest?.successful],
              ["Отсутствуют", latest?.missing],
              ["С ошибками", latest?.errors],
            ].map(([label, value]) => (
              <div className="field" key={String(label)}>
                <span>{label}</span>
                <b>{value ?? "—"}</b>
              </div>
            ))}
          </div>
          {latest?.error_code && (
            <p>
              Причина недоступности:{" "}
              {reasons[latest.error_code] || latest.error_code}
            </p>
          )}
          <p className="muted">
            Проверка сравнивает метаданные обработанных файлов. Она не загружает
            файлы и не запускает импорт. Файлы ожидаются до 00:20 следующего дня по Москве. Незавершённая обработка не считается пропуском.
          </p>
          <div className="filter-grid">
            <label>
              Дата продаж с
              <input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
              />
            </label>
            <label>
              По
              <input
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
              />
            </label>
            <button disabled={busy} onClick={() => void action(refresh)}>
              Обновить проверку
            </button>
          </div>
          <label>
            Причина ручного подтверждения окончания цикла
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <button
            disabled={busy || !from || from !== to || reason.trim().length < 5}
            onClick={() =>
              void action(async () => {
                await api(`/import-control/cycles/${from}/confirm`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ reason }),
                });
                await load();
              })
            }
          >
            Подтвердить окончание выбранного дня
          </button>
        </>
      )}
      {tab === "departments" && (
        <>
          <div className="section-title">
            <p>
              Правила меняются с указанной даты; исторические интервалы
              сохраняются.
            </p>
            <button disabled={busy} onClick={() => edit()}>
              Добавить подразделение
            </button>
          </div>
          <button
            onClick={() =>
              void action(async () =>
                setCandidates(
                  (await api<any>("/import-control/candidates")).items,
                ),
              )
            }
          >
            Предварительный список из журнала
          </button>
          {candidates && (
            <details open>
              <summary>Неподтверждённые файлы (до 200 импортов)</summary>
              <p>
                Названия подразделений и даты работы нужно утвердить вручную.
              </p>
              {candidates.map((r: any) => (
                <p key={r.import_id}>
                  {r.filename} · {r.period_start || "Период неизвестен"} ·{" "}
                  {r.status}
                </p>
              ))}
            </details>
          )}
          {!departments.length ? (
            <p>Подразделения не настроены. Строгий контроль включать нельзя.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Код</th>
                    <th>Название</th>
                    <th>Ожидаемый файл</th>
                    <th>Интервал</th>
                    <th>Контроль</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {departments.map((row) => {
                    const r = row.rules[row.rules.length - 1];
                    return (
                      <tr key={row.id}>
                        <td>{row.code}</td>
                        <td>{row.name}</td>
                        <td>{r?.filename_template}</td>
                        <td>
                          {r?.start_date} — {r?.end_date || "без окончания"}
                        </td>
                        <td>
                          {r?.active && r?.controlled ? "Включён" : "Отключён"}
                        </td>
                        <td>
                          <button onClick={() => edit(row)}>Изменить</button>
                          <button
                            onClick={() =>
                              void action(async () => {
                                await loadAudit(row.id);
                              })
                            }
                          >
                            История
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {tab === "settings" && config && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void action(async () => {
              const { revision, ...payload } = config;
              await api("/import-control/settings", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...payload, approve }),
              });
              await load();
            });
          }}
        >
          <p>
            Включать только после утверждения справочника и даты начала.
            Выгрузка ожидается в 23:40. Контрольный срок — 00:20 следующего дня по Москве. Маркер не требуется.
          </p>
          <div className="filter-grid">
            <label>
              Начало контроля
              <input
                type="date"
                value={config.start_date || ""}
                onChange={(e) =>
                  setConfig({ ...config, start_date: e.target.value || null })
                }
              />
            </label>
          </div>
          <label>
            Получатели email (по одному адресу в строке)
            <textarea
              value={config.recipients}
              onChange={(e) =>
                setConfig({ ...config, recipients: e.target.value })
              }
            />
          </label>
          <div className="import-control-options">
            {[
              ["enabled", "Контроль включён"],
              ["automatic", "Автоматические проверки"],
              ["email_enabled", "Email-уведомления"],
            ].map(([key, label]) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={Boolean(config[key as keyof Config])}
                  onChange={(e) =>
                    setConfig({ ...config, [key]: e.target.checked })
                  }
                />
                {label}
              </label>
            ))}
            <label>
              <input
                type="checkbox"
                checked={approve}
                onChange={(e) => setApprove(e.target.checked)}
              />
              Подтверждаю справочник, дату начала и контрольный срок 00:20 МСК
            </label>
          </div>
          <button className="primary" disabled={busy}>
            Сохранить настройки контроля
          </button>
          <p className="muted">
            SMTP используется из существующих настроек. Пароли здесь не
            отображаются.
          </p>
        </form>
      )}
      {tab === "notices" && (
        <>
          <p>
            Неопределённая доставка не повторяется автоматически: сначала
            проверьте SMTP-журнал.
          </p>
          {!notices.length ? (
            <p>Уведомлений пока нет</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Дата продаж</th>
                    <th>Получатели</th>
                    <th>Статус</th>
                    <th>Попытки</th>
                    <th>Причина</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {notices.map((r: any) => (
                    <tr key={r.id}>
                      <td>{date(r.sales_date)}</td>
                      <td>{r.recipients}</td>
                      <td>{labels[r.status] || r.status}</td>
                      <td>{r.attempts}</td>
                      <td>{r.error_code}</td>
                      <td>
                        {["failed", "uncertain"].includes(r.status) && (
                          <button
                            disabled={busy || reason.trim().length < 5}
                            onClick={() =>
                              void action(async () => {
                                await api(
                                  `/import-control/notices/${r.id}/retry`,
                                  {
                                    method: "POST",
                                    headers: {
                                      "Content-Type": "application/json",
                                    },
                                    body: JSON.stringify({ reason }),
                                  },
                                );
                                await loadNotices(noticePage);
                              })
                            }
                          >
                            Повторить вручную
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <label>
            Обоснование повтора (при неопределённой доставке возможен дубль)
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="section-title">
            <button
              disabled={busy || noticePage <= 1}
              onClick={() => void action(() => loadNotices(noticePage - 1))}
            >
              Предыдущие уведомления
            </button>
            <span>
              Страница {noticePage} · Всего {noticeTotal}
            </span>
            <button
              disabled={busy || noticePage * 50 >= noticeTotal}
              onClick={() => void action(() => loadNotices(noticePage + 1))}
            >
              Следующие уведомления
            </button>
          </div>
        </>
      )}
      {modal === "department" && (
        <Modal
          focusTarget={focusReturn.current}
          title={
            form.id
              ? "Изменить правило подразделения"
              : "Добавить подразделение"
          }
          close={() => setModal("")}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action(saveDepartment);
            }}
          >
            <div className="filter-grid">
              <label>
                Код подразделения
                <input
                  required
                  disabled={!!form.id}
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                />
              </label>
              <label>
                Название
                <input
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label>
                Дата вступления правила
                <input
                  required
                  type="date"
                  value={form.start_date}
                  onChange={(e) =>
                    setForm({ ...form, start_date: e.target.value })
                  }
                />
              </label>
              <label>
                Последняя дата продаж
                <input
                  type="date"
                  value={form.end_date}
                  onChange={(e) =>
                    setForm({ ...form, end_date: e.target.value })
                  }
                />
              </label>
              <label>
                Точное имя или шаблон с {"{date}"}
                <input
                  required
                  value={form.filename_template}
                  onChange={(e) =>
                    setForm({ ...form, filename_template: e.target.value })
                  }
                />
              </label>
              <label>
                Формат даты в имени
                <select
                  value={form.date_format}
                  onChange={(e) =>
                    setForm({ ...form, date_format: e.target.value })
                  }
                >
                  <option value="dmy">ДД.ММ.ГГГГ</option>
                  <option value="iso">ГГГГ-ММ-ДД</option>
                </select>
              </label>
            </div>
            <label>
              <input
                type="checkbox"
                checked={form.active}
                onChange={(e) => setForm({ ...form, active: e.target.checked })}
              />
              Активно
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.controlled}
                onChange={(e) =>
                  setForm({ ...form, controlled: e.target.checked })
                }
              />
              Контроль импорта
            </label>
            <p>
              Отключение вступает в силу с даты правила, не отменяя прежние
              пропуски.
            </p>
            <button className="primary" disabled={busy}>
              Сохранить подразделение
            </button>
            {error && <p role="alert">{error}</p>}
          </form>
        </Modal>
      )}
      {modal === "issues" && (
        <Modal
          focusTarget={focusReturn.current}
          title="Отсутствующие файлы импорта"
          close={() => setModal("")}
        >
          <div className="filter-grid">
            <label>
              С даты
              <input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
              />
            </label>
            <label>
              По дату
              <input
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
              />
            </label>
            <label>
              Подразделение
              <select
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
              >
                <option value="">Все</option>
                {departments.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Статус
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {[
                  ["open", "Все незакрытые"],
                  ["missing", "Отсутствует"],
                  ["error", "Ошибка обработки"],
                  ["resolved", "Устранено"],
                  ["closed", "Закрыто администратором"],
                  ["all", "Все статусы"],
                ].map(([key, value]) => (
                  <option key={key} value={key}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Сортировка
              <select
                value={direction}
                onChange={(e) => setDirection(e.target.value)}
              >
                <option value="desc">Сначала новые</option>
                <option value="asc">Сначала старые</option>
              </select>
            </label>
          </div>
          <button
            disabled={busy}
            onClick={() => void action(() => loadIssues(1))}
          >
            Применить фильтры
          </button>
          <button disabled={busy} onClick={() => void action(refresh)}>
            Обновить проверку
          </button>
          <p className="muted">
            Без периода повторно проверяются до 31 открытой даты, проверявшейся
            раньше остальных за одно нажатие. Загрузка файлов не запускается.
          </p>
          <p>
            Записей: {issues?.total ?? 0} · Последняя проверка:{" "}
            {issues?.last_checked_at ? datetime(issues.last_checked_at) : "—"}
          </p>
          <label>
            Причина административного закрытия
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Дата продаж</th>
                  <th>Подразделение</th>
                  <th>Ожидаемый файл</th>
                  <th>Статус</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {issues?.items.map((r) => (
                  <tr key={r.id}>
                    <td>{date(r.sales_date)}</td>
                    <td>{r.department_name}</td>
                    <td>{r.expected_filename}</td>
                    <td>
                      {r.status === "error"
                        ? "Файл получен, но не импортирован"
                        : labels[r.status] || r.status}
                    </td>
                    <td>
                      <button
                        onClick={() =>
                          void action(async () =>
                            setDetails(
                              await api(
                                `/import-control/issues/${r.id}/details`,
                              ),
                            ),
                          )
                        }
                      >
                        Детали
                      </button>
                      {["missing", "error"].includes(r.status) && (
                        <button
                          disabled={busy || reason.trim().length < 5}
                          onClick={() => void action(() => closeIssue(r))}
                        >
                          Закрыть с причиной
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!issues?.items.length && <p>Пропусков по выбранным условиям нет</p>}
          <div className="section-title">
            <button
              disabled={busy || page <= 1}
              onClick={() => void action(() => loadIssues(page - 1))}
            >
              Назад
            </button>
            <span>Страница {page}</span>
            <button
              disabled={busy || page * 50 >= (issues?.total || 0)}
              onClick={() => void action(() => loadIssues(page + 1))}
            >
              Далее
            </button>
          </div>
          {details && (
            <details open>
              <summary>Детали обработки</summary>
              <p>
                Причина:{" "}
                {reasons[details.issue?.error_code] ||
                  details.issue?.error_code ||
                  "Файл не получен"}
              </p>
              {!details.receipts?.length && (
                <p>Подтверждённых попыток обработки нет.</p>
              )}
              {details.receipts?.map((r: any) => (
                <article key={r.id}>
                  <b>
                    Импорт #{r.import_id}: {r.filename}
                  </b>
                  <p>
                    {labels[r.status] || r.status} · Ошибок строк:{" "}
                    {r.error_rows || 0}
                  </p>
                  <p>
                    {r.error_text ||
                      reasons[r.error_code] ||
                      r.error_code ||
                      "Ошибок не зафиксировано"}
                  </p>
                </article>
              ))}
            </details>
          )}
          {error && <p role="alert">{error}</p>}
        </Modal>
      )}
      {modal === "audit" && (
        <Modal
          focusTarget={focusReturn.current}
          title="История изменений подразделения"
          close={() => setModal("")}
        >
          {audit?.items?.map((r: any) => (
            <article key={r.id}>
              <b>
                {datetime(r.created_at)} · {r.actor}
              </b>
              <p>Вступает в силу: {r.effective_date}</p>
              <pre>{JSON.stringify(r.changes, null, 2)}</pre>
            </article>
          ))}
          <div className="section-title">
            <button
              disabled={busy || !auditDepartment || audit?.page <= 1}
              onClick={() =>
                void action(() => loadAudit(auditDepartment!, audit.page - 1))
              }
            >
              Предыдущие изменения
            </button>
            <span>
              Страница {audit?.page || 1} · Всего {audit?.total || 0}
            </span>
            <button
              disabled={
                busy ||
                !auditDepartment ||
                (audit?.page || 1) * 50 >= (audit?.total || 0)
              }
              onClick={() =>
                void action(() => loadAudit(auditDepartment!, audit.page + 1))
              }
            >
              Следующие изменения
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
