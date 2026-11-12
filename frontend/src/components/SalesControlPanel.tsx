import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, query } from "../api/client";
import { RefreshCw, Search, X } from "lucide-react";
export type MissingRow = {
  department: string;
  sales_date: string;
  status: string;
};
type Result = {
  items: MissingRow[];
  total: number;
  page: number;
  date_from: string;
  date_to: string;
};
export function defaultPeriod() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  const today = `${get("year")}-${get("month")}-${get("day")}`;
  const end = new Date(`${today}T12:00:00Z`);
  end.setUTCDate(end.getUTCDate() - 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 29);
  return {
    date_from: start.toISOString().slice(0, 10),
    date_to: end.toISOString().slice(0, 10),
  };
}
const rowKey = (r: MissingRow) => JSON.stringify([r.department, r.sales_date]);
const showDate = (s: string) => s.split("-").reverse().join(".");
function Confirm({
  count,
  restore,
  busy,
  onClose,
  onConfirm,
}: {
  count: number;
  restore: boolean;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const last = document.activeElement as HTMLElement | null;
    const node = ref.current!;
    node.showModal?.();
    if (!node.open) node.setAttribute("open", "");
    node.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      node.close?.();
      last?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="sales-confirm"
      aria-labelledby="sales-confirm-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <h3 id="sales-confirm-title">
        {restore ? "Восстановить" : "Удалить"} {count} записей?
      </h3>
      <p>
        {restore
          ? "Выбранные даты снова будут учитываться в контроле."
          : "Выбранные записи будут исключены из контроля продаж. Сами продажи и подразделения не изменятся."}
      </p>
      <div className="button-row">
        <button disabled={busy} onClick={onClose}>
          Отмена
        </button>
        <button className="primary" disabled={busy} onClick={onConfirm}>
          {busy ? "Сохраняем…" : restore ? "Восстановить" : "Удалить"}
        </button>
      </div>
    </dialog>
  );
}
export function SalesControlPanel() {
  const [params, setParams] = useSearchParams();
  const defaults = defaultPeriod();
  const start = params.get("date_from") || defaults.date_from,
    end = params.get("date_to") || defaults.date_to,
    department = params.get("department") || "",
    search = params.get("search") || "",
    hidden = params.get("hidden") === "true",
    page = Math.max(1, Number(params.get("page")) || 1),
    sort = params.get("sort") || "date",
    direction = params.get("direction") || "desc";
  const [hiddenUI, setHiddenUI] = useState(hidden);
  useEffect(() => setHiddenUI(hidden), [hidden]);
  const [input, setInput] = useState(search),
    [data, setData] = useState<Result>(),
    [deps, setDeps] = useState<{ department: string }[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [confirm, setConfirm] = useState(false),
    [busy, setBusy] = useState(false),
    [refresh, setRefresh] = useState(0);
  const inflight = useRef(false),
    selectAll = useRef<HTMLInputElement>(null),
    signature = JSON.stringify([
      start,
      end,
      department,
      search,
      hidden,
      page,
      sort,
      direction,
    ]);
  const update = useCallback(
    (values: Record<string, string>) => {
      setSelected(new Set());
      setConfirm(false);
      const next = new URLSearchParams(params);
      Object.entries(values).forEach(([k, v]) =>
        v ? next.set(k, v) : next.delete(k),
      );
      if (!("page" in values)) next.delete("page");
      setHiddenUI(next.get("hidden") === "true");
      if (next.toString() !== params.toString()) {
        setLoading(true);
        setParams(next, { replace: true });
      }
    },
    [params, setParams],
  );
  useEffect(() => setInput(search), [search]);
  useEffect(() => {
    if (input === search) return;
    const timer = setTimeout(() => update({ search: input }), 300);
    return () => clearTimeout(timer);
  }, [input, search, update]);
  useEffect(() => {
    let live = true;
    api<{ department: string }[]>("/sales-control/departments")
      .then((x) => {
        if (live) setDeps(x);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [refresh]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setSelected(new Set());
    setConfirm(false);
    api<Result>(
      "/sales-control/missing?" +
        query({
          date_from: start,
          date_to: end,
          department,
          search,
          hidden,
          page,
          sort,
          direction,
        }),
      { signal: controller.signal },
    )
      .then((x) => {
        if (!controller.signal.aborted) setData(x);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [signature, refresh]);
  const rows = data?.items || [],
    all = rows.length > 0 && rows.every((r) => selected.has(rowKey(r)));
  useEffect(() => {
    if (selectAll.current)
      selectAll.current.indeterminate = selected.size > 0 && !all;
  }, [selected, all]);
  async function change() {
    if (inflight.current || loading) return;
    inflight.current = true;
    setBusy(true);
    try {
      await api("/sales-control/exclusions", {
        method: hidden ? "DELETE" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approve: true,
          items: rows
            .filter((r) => selected.has(rowKey(r)))
            .map(({ department, sales_date }) => ({ department, sales_date })),
        }),
      });
      setConfirm(false);
      setSelected(new Set());
      setRefresh((x) => x + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  function reset() {
    const d = defaultPeriod();
    update({
      ...d,
      department: "",
      search: "",
      hidden: "",
      page: "",
      sort: "",
      direction: "",
    });
    setInput("");
  }
  return (
    <section
      className="panel sales-control"
      aria-labelledby="sales-control-title"
    >
      <div className="sales-heading">
        <div>
          <h2 id="sales-control-title">Контроль продаж</h2>
          <p className="hint">
            Даты, за которые отсутствуют продажи по подразделениям
          </p>
        </div>
        <div>
          <span aria-live="polite">
            {!loading && !error && `Найдено: ${data?.total ?? 0}`}
          </span>
          <button
            onClick={() => setRefresh((x) => x + 1)}
            disabled={loading || busy}
          >
            <RefreshCw size={16} /> Обновить
          </button>
        </div>
      </div>
      <div className="sales-filters">
        <label>
          Дата с
          <input
            type="date"
            value={start}
            max={end}
            onChange={(e) => update({ date_from: e.target.value })}
          />
        </label>
        <label>
          Дата по
          <input
            type="date"
            value={end}
            min={start}
            max={defaults.date_to}
            onChange={(e) => update({ date_to: e.target.value })}
          />
        </label>
        <label>
          Подразделение
          <select
            value={department}
            onChange={(e) => update({ department: e.target.value })}
          >
            <option value="">Все подразделения</option>
            {deps.map((d) => (
              <option key={d.department}>{d.department}</option>
            ))}
          </select>
        </label>
        <label className="sales-search">
          Поиск
          <div>
            <Search size={16} />
            <input
              value={input}
              onChange={(e) => {
                setSelected(new Set());
                setInput(e.target.value);
              }}
              placeholder="Поиск по подразделению или дате"
            />
            {input && (
              <button
                aria-label="Очистить поиск"
                onClick={() => {
                  setInput("");
                  update({ search: "" });
                }}
              >
                <X size={14} />
              </button>
            )}
          </div>
        </label>
        <button onClick={reset}>Сбросить фильтры</button>
      </div>
      <p className="hint">
        Все календарные дни, начиная с первой продажи подразделения. Сегодня не
        проверяется. Максимальный период — 366 дней.
      </p>
      <div className="sales-actions">
        <label>
          <input
            type="checkbox"
            checked={hiddenUI}
            onChange={(e) => update({ hidden: e.target.checked ? "true" : "" })}
          />{" "}
          Показать скрытые
        </label>
        <span>{selected.size > 0 && `Выбрано: ${selected.size}`}</span>
        <button
          disabled={!selected.size || busy || loading}
          onClick={() => setConfirm(true)}
        >
          {hidden ? "Восстановить" : "Удалить"}
        </button>
      </div>
      {error ? (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setRefresh((x) => x + 1)}>Повторить</button>
        </div>
      ) : loading ? (
        <p role="status">Загружаем контроль продаж…</p>
      ) : rows.length ? (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>
                    <input
                      ref={selectAll}
                      type="checkbox"
                      aria-label="Выделить все строки текущей страницы"
                      checked={all}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? new Set(rows.map(rowKey))
                            : new Set(),
                        )
                      }
                    />
                  </th>
                  <th>
                    <button
                      onClick={() =>
                        update({
                          sort: "date",
                          direction:
                            sort === "date" && direction === "desc"
                              ? "asc"
                              : "desc",
                        })
                      }
                    >
                      Дата{" "}
                      {sort === "date"
                        ? direction === "desc"
                          ? "↓"
                          : "↑"
                        : ""}
                    </button>
                  </th>
                  <th>
                    <button
                      onClick={() =>
                        update({
                          sort: "department",
                          direction:
                            sort === "department" && direction === "asc"
                              ? "desc"
                              : "asc",
                        })
                      }
                    >
                      Подразделение{" "}
                      {sort === "department"
                        ? direction === "asc"
                          ? "↑"
                          : "↓"
                        : ""}
                    </button>
                  </th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={rowKey(r)}
                    className={selected.has(rowKey(r)) ? "selected" : ""}
                  >
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Выбрать ${r.department} ${showDate(r.sales_date)}`}
                        checked={selected.has(rowKey(r))}
                        onChange={(e) =>
                          setSelected((prev) => {
                            const x = new Set(prev);
                            e.target.checked
                              ? x.add(rowKey(r))
                              : x.delete(rowKey(r));
                            return x;
                          })
                        }
                      />
                    </td>
                    <td className="sales-date">{showDate(r.sales_date)}</td>
                    <td>{r.department}</td>
                    <td>
                      <span className="sales-missing-badge">
                        Продажи отсутствуют
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sales-pagination">
            <span>
              Страница {page} из{" "}
              {Math.max(1, Math.ceil((data?.total || 0) / 50))} · Всего{" "}
              {data?.total}
            </span>
            <button
              disabled={page <= 1}
              onClick={() => update({ page: String(page - 1) })}
            >
              Назад
            </button>
            <button
              disabled={page * 50 >= (data?.total || 0)}
              onClick={() => update({ page: String(page + 1) })}
            >
              Вперёд
            </button>
          </div>
        </>
      ) : (
        <div className="sales-empty">
          <h3>
            {search || department || hidden
              ? "По выбранным условиям записей не найдено"
              : "Отсутствующих продаж не найдено"}
          </h3>
          <p>
            {!search && !department && !hidden
              ? "За выбранный период продажи присутствуют по всем контролируемым подразделениям."
              : "Попробуйте изменить фильтры."}
          </p>
        </div>
      )}
      {confirm && (
        <Confirm
          count={selected.size}
          restore={hidden}
          busy={busy}
          onClose={() => setConfirm(false)}
          onConfirm={() => void change()}
        />
      )}
    </section>
  );
}
