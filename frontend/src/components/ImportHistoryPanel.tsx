import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { date, datetime } from "../utils/format";

type Department = {
  id: number;
  name: string;
  code: string;
  rules: { start_date: string }[];
};
type Plan = {
  department_id: number;
  first_rule_date: string;
  date_from: string;
  date_to: string;
};
type Job = {
  status: string;
  checked: number;
  total: number;
  plan: Plan[];
  next_date?: string;
  error_code?: string;
  updated_at?: string;
};
type Result = {
  sales_date: string;
  department_id: number;
  department_name: string;
  department_code: string;
  expected_filename: string | null;
  status: string;
  checked_at: string;
  error_code?: string;
};
type Proposal = {
  token: string;
  items: {
    rule_id: number;
    department_code: string;
    start_date: string;
    end_date: string | null;
    before: string;
    after: string;
  }[];
};
const statusLabels: Record<string, string> = {
  not_started: "Не запускалась",
  running: "Работает",
  paused: "Приостановлена",
  completed: "Завершена",
  error: "Ошибка",
  success: "Загружен",
  missing: "Не загружен",
  unavailable: "Требует проверки",
  waiting: "Ожидание срока или завершения импорта",
  not_expected: "Файл не ожидается",
};
const reasonLabels: Record<string, string> = {
  rules_changed: "Правила изменились — начните новую сверку",
  resources_or_database: "Проверка приостановлена из-за ресурсов или ошибки БД",
  historical_template_requires_review:
    "Нужно проверить исторический шаблон имени файла",
  unverified_period: "Период успешного импорта не подтверждён",
  rule_no_longer_applies: "Правило больше не действует на эту дату",
};
export function ImportHistoryPanel({
  departments,
}: {
  departments: Department[];
}) {
  const [job, setJob] = useState<Job>(),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [department, setDepartment] = useState(""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [status, setStatus] = useState("all"),
    [approve, setApprove] = useState(false);
  const [rows, setRows] = useState<{
      items: Result[];
      total: number;
      page: number;
    }>(),
    [proposal, setProposal] = useState<Proposal>(),
    [chosen, setChosen] = useState<number[]>([]),
    [reason, setReason] = useState(""),
    [repairApprove, setRepairApprove] = useState(false);
  const busyRef = useRef(false);
  const loadStatus = async () =>
    setJob(await api<Job>("/import-control/historical/status"));
  async function action(fn: () => Promise<unknown>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось выполнить проверку",
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  useEffect(() => {
    let active = true;
    api<Job>("/import-control/historical/status")
      .then((j) => {
        if (active) setJob(j);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (job?.status !== "running") return;
    const id = setInterval(
      () => void loadStatus().catch((e) => setError(e.message)),
      10000,
    );
    return () => clearInterval(id);
  }, [job?.status]);
  async function results(page = 1) {
    const query = new URLSearchParams({ page: String(page), status });
    if (department) query.set("department_id", department);
    if (from) query.set("date_from", from);
    if (to) query.set("date_to", to);
    setRows(await api(`/import-control/historical/results?${query}`));
  }
  async function post(path: string, body?: unknown) {
    return api(`/import-control/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }
  return (
    <section
      className="import-history-panel"
      aria-label="Историческая сверка импорта"
    >
      <h3>Историческая сверка</h3>
      <p className="hint">
        Начало истории определяется первым правилом каждого подразделения. Дата
        включения ежедневного контроля её не ограничивает. Историческая сверка
        не отправляет письма и не загружает файлы повторно. До 00:20 МСК
        следующего дня файл ещё ожидается.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <p role="status" className="history-status">
        Состояние: {statusLabels[job?.status || "not_started"] || job?.status}.
        Проверено дней подразделений: {job?.checked || 0} из {job?.total || 0}
        {job?.next_date ? `; следующая дата: ${date(job.next_date)}` : ""}.{" "}
        {job?.updated_at && `Проверено: ${datetime(job.updated_at)}`}
      </p>
      <progress
        className="history-progress"
        aria-label="Прогресс исторической сверки"
        max={Math.max(job?.total || 0, 1)}
        value={Math.min(Math.max(job?.checked || 0, 0), job?.total || 0)}
      />
      {job?.error_code && (
        <p role="alert">
          {reasonLabels[job.error_code] ||
            "Ошибка проверки; изучите диагностику"}
        </p>
      )}
      <div className="filters">
        <label>
          Подразделение для исторической сверки
          <select
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
          >
            <option value="">Все подразделения</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.code})
              </option>
            ))}
          </select>
        </label>
        <label>
          История с (пусто — с первого правила)
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label>
          История по (пусто — сегодня)
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
      </div>
      <div className="history-action-card">
        <label>
          <input
            type="checkbox"
            checked={approve}
            onChange={(e) => setApprove(e.target.checked)}
          />
          Подтверждаю запуск или новый пересчёт выбранной истории
        </label>
        <div className="actions">
          <button
            className="primary"
            disabled={busy || !approve || job?.status === "running"}
            onClick={() =>
              void action(async () => {
                setJob(
                  (await post("historical/start", {
                    department_id: department ? Number(department) : null,
                    date_from: from || null,
                    date_to: to || null,
                    approve: true,
                  })) as Job,
                );
                setApprove(false);
              })
            }
          >
            Начать сверку с правил
          </button>
          {job?.status === "running" && (
            <button
              disabled={busy || job?.status !== "running"}
              onClick={() =>
                void action(async () =>
                  setJob((await post("historical/pause")) as Job),
                )
              }
            >
              Приостановить сверку
            </button>
          )}
          {job && ["paused", "error"].includes(job.status) && (
            <button
              disabled={
                busy || !job || !["paused", "error"].includes(job.status)
              }
              onClick={() =>
                void action(async () =>
                  setJob((await post("historical/resume")) as Job),
                )
              }
            >
              Продолжить сверку
            </button>
          )}
          <button disabled={busy} onClick={() => void action(loadStatus)}>
            Обновить прогресс
          </button>
        </div>
      </div>
      <details open className="history-plan">
        <summary>План по подразделениям</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Подразделение</th>
                <th>Код</th>
                <th>Первое правило</th>
                <th>Проверяемый период</th>
              </tr>
            </thead>
            <tbody>
              {departments
                .filter((d) => !department || d.id === Number(department))
                .map((d) => {
                  const p = job?.plan?.find((p) => p.department_id === d.id);
                  const first = d.rules.map((r) => r.start_date).sort()[0];
                  return (
                    <tr key={d.id}>
                      <td>{d.name}</td>
                      <td>{d.code}</td>
                      <td>{first ? date(first) : "Нет правил"}</td>
                      <td>
                        {p
                          ? `${date(p.date_from)} — ${date(p.date_to)}`
                          : "Не запускалась"}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </details>
      <h3>Результаты сверки</h3>
      <p className="hint">
        Выберите статус и запросите сохранённые результаты. Просмотр не
        запускает новую сверку.
      </p>
      <div className="history-result-tools">
        {" "}
        <label>
          Статус файла
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {[
              "all",
              "success",
              "missing",
              "error",
              "unavailable",
              "waiting",
              "not_expected",
            ].map((s) => (
              <option key={s} value={s}>
                {s === "all"
                  ? "Все статусы"
                  : s === "error"
                    ? "Ошибка импорта"
                    : statusLabels[s]}
              </option>
            ))}
          </select>
        </label>{" "}
        <button disabled={busy} onClick={() => void action(() => results())}>
          Показать результаты сверки
        </button>
      </div>
      {rows && (
        <>
          <p>
            Результатов: {rows.total}. Пропуски также доступны в общей истории
            пропусков.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Дата продаж</th>
                  <th>Подразделение / код</th>
                  <th>Ожидаемый файл</th>
                  <th>Статус</th>
                  <th>Последняя проверка</th>
                </tr>
              </thead>
              <tbody>
                {rows.items.map((r) => (
                  <tr key={`${r.sales_date}-${r.department_id}`}>
                    <td>{date(r.sales_date)}</td>
                    <td>
                      {r.department_name} / {r.department_code}
                    </td>
                    <td>{r.expected_filename || "Не ожидается"}</td>
                    <td>
                      {r.status === "error"
                        ? "Ошибка импорта"
                        : statusLabels[r.status]}
                      {r.error_code && (
                        <small>
                          {reasonLabels[r.error_code] ||
                            "Причина указана в журнале импорта"}
                        </small>
                      )}
                    </td>
                    <td>{datetime(r.checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!rows.items.length && (
            <p>Проверенных файлов по выбранным условиям нет</p>
          )}
          <div className="history-pagination">
            <button
              disabled={busy || rows.page <= 1}
              onClick={() => void action(() => results(rows.page - 1))}
            >
              Предыдущая страница сверки
            </button>
            <button
              disabled={busy || rows.page * 50 >= rows.total}
              onClick={() => void action(() => results(rows.page + 1))}
            >
              Следующая страница сверки
            </button>
          </div>
        </>
      )}
      <details className="import-advanced">
        <summary>Исправление исторических шаблонов</summary>
        <div>
          <p className="hint">
            Сначала просмотрите предложения. Применение сохраняет даты правил,
            проверяет конфликты и записывает аудит. Продажи и реестр импортов не
            меняются.
          </p>
          <button
            disabled={busy}
            onClick={() =>
              void action(async () => {
                setProposal(
                  await api<Proposal>(
                    "/import-control/template-repair/preview",
                  ),
                );
                setChosen([]);
                setRepairApprove(false);
              })
            }
          >
            Подготовить отчёт исправления шаблонов
          </button>
          {proposal && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Выбрать</th>
                      <th>Код / правило</th>
                      <th>Действует</th>
                      <th>Старый шаблон</th>
                      <th>Предложение</th>
                    </tr>
                  </thead>
                  <tbody>
                    {proposal.items.map((r) => (
                      <tr key={r.rule_id}>
                        <td>
                          <input
                            aria-label={`Исправить правило ${r.rule_id}`}
                            type="checkbox"
                            checked={chosen.includes(r.rule_id)}
                            onChange={(e) =>
                              setChosen(
                                e.target.checked
                                  ? [...chosen, r.rule_id]
                                  : chosen.filter((id) => id !== r.rule_id),
                              )
                            }
                          />
                        </td>
                        <td>
                          {r.department_code} / {r.rule_id}
                        </td>
                        <td>
                          {date(r.start_date)} —{" "}
                          {r.end_date ? date(r.end_date) : "действует"}
                        </td>
                        <td>{r.before}</td>
                        <td>{r.after}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!proposal.items.length && (
                <p>Подходящих исторических шаблонов нет</p>
              )}
              <label>
                Причина исправления
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={repairApprove}
                  onChange={(e) => setRepairApprove(e.target.checked)}
                />
                Подтверждаю выбранные исправления исторических шаблонов
              </label>
              <button
                disabled={
                  busy ||
                  !repairApprove ||
                  !chosen.length ||
                  reason.trim().length < 5 ||
                  job?.status === "running"
                }
                onClick={() =>
                  void action(async () => {
                    await post("template-repair/apply", {
                      token: proposal.token,
                      rule_ids: chosen,
                      reason,
                      approve: true,
                    });
                    setProposal(undefined);
                    setRepairApprove(false);
                    await loadStatus();
                  })
                }
              >
                Применить выбранные исправления
              </button>
            </>
          )}
        </div>
      </details>
    </section>
  );
}
