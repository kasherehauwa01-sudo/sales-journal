import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  Line,
  LineChart,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  CategoryTreeFilter,
  type CategoryNode,
} from "../components/CategoryTreeFilter";
import { MultiSelect } from "../components/MultiSelect";
import { reportJson } from "../hooks/useExplicitReport";

type Cell = {
  index: number | null;
  stores: string[];
  years: number[];
  estimated: boolean;
  reason: string | null;
  notes: string[];
};
type Actual = {
  store: string;
  year: number;
  month: number;
  value: number;
  included: boolean;
  method: string;
  reason: string | null;
};
type Profile = {
  cells: Cell[];
  peaks: number[];
  troughs: number[];
  actual: Actual[];
  estimated: boolean;
};
type Row = {
  key: string;
  parent_key: string;
  parent: string;
  name: string;
  units: Profile;
  revenue: Profile;
};
type Params = {
  years: number[];
  stores: string[];
  categories: string[];
  brands: string[];
  manufacturers: string[];
  manager: string[];
  buyer_type: string[];
};
type Options = {
  years: number[];
  stores: string[];
  nodes: CategoryNode[];
  brand: string[];
  manufacturer: string[];
  managers: string[];
  buyer_types: string[];
};
type Result = {
  state: "ready" | "uninitialized";
  rows: Row[];
  options: Partial<Options>;
  filters: Params;
  snapshot_id: string | null;
  last_success: string | null;
  through: string | null;
  next_update: string;
  updating: boolean;
  previous_after_error: boolean;
  warning?: string;
};
const months = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];
const signed = (n: number | null) =>
  n !== null && n !== 0 && Math.abs(n) < 0.05
    ? `${n > 0 ? "+" : "−"}<0,1%`
    : n == null
      ? "Нет данных"
      : `${n > 0 ? "+" : ""}${n.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
const value = (n: number) =>
  n.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
const moscow = (s: string | null) =>
  s ? new Date(s).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) : "—";
function tooltip(c: Cell, m: number) {
  return `${months[m]}: ${signed(c.index)}. Магазины (${c.stores.length}): ${c.stores.join(", ") || "нет"}. Годы (${c.years.length}): ${c.years.join(", ") || "нет"}. ${c.estimated ? "Ориентировочный профиль. " : ""}${c.notes.join(". ")}${c.reason ? ". " + c.reason : ""}`;
}
function heat(c: Cell) {
  if (c.index === null) return undefined;
  const n = c.index;
  if (Math.abs(n) < 1) return { background: "#f1f4f7", color: "#304357" };
  const a = Math.min(0.65, 0.12 + Math.abs(n) / 250);
  return {
    background: n > 0 ? `rgba(20,150,95,${a})` : `rgba(38,116,210,${a})`,
    color: Math.abs(n) > 100 ? "#102334" : "#203c53",
  };
}

function Detail({
  row,
  metric,
  filters,
  origin,
  onClose,
  snapshotId,
}: {
  row: Row;
  metric: "units" | "revenue";
  filters: Params;
  origin: HTMLElement | null;
  onClose: () => void;
  snapshotId: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => {
      dialog.current?.close();
      origin?.focus();
    };
  }, []);
  const [detail, setDetail] = useState<Row>(),
    [detailError, setDetailError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setDetailError("");
    void reportJson<Result>(
      "/reports/category-seasonality/detail",
      controller.signal,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...filters,
          categories: [row.key],
          snapshot_id: snapshotId,
        }),
      },
    )
      .then((result) => {
        if (result.rows.length !== 1)
          throw new Error("Детализация снимка недоступна. Обновите карту.");
        if (!controller.signal.aborted) setDetail(result.rows[0]);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setDetailError(e instanceof Error ? e.message : "Ошибка детализации");
      });
    return () => controller.abort();
  }, [row.key, snapshotId, retry]);
  const current = detail ?? row;
  const p = current[metric],
    points = p.cells.map((c, i) => ({
      month: months[i].slice(0, 3),
      index: c.index,
    }));
  const rows = [
    ...new Set(p.actual.map((a) => JSON.stringify([a.store, a.year]))),
  ];
  const lookup = (
    metric: "units" | "revenue",
    store: string,
    year: number,
    month: number,
  ) =>
    current[metric].actual.find(
      (a) => a.store === store && a.year === year && a.month === month,
    );
  const labels: Record<string, string> = {
    years: "Годы",
    stores: "Магазин",
    categories: "Ветки каталога",
    brands: "Бренд",
    manufacturers: "Производитель",
    manager: "Менеджер",
    buyer_type: "Вид покупателя",
  };
  return (
    <dialog
      className="category-season-detail"
      ref={dialog}
      aria-labelledby="cs-detail-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div className="category-season-detail-head">
        <div>
          <small>{row.parent}</small>
          <h2 id="cs-detail-title">{row.name}</h2>
        </div>
        <button autoFocus onClick={onClose}>
          Закрыть
        </button>
      </div>
      <p>Применённые фильтры опубликованного снимка:</p>
      <dl className="category-season-filters">
        {Object.entries(filters)
          .filter(([k]) => k !== "snapshot_id")
          .map(([k, v]) => (
            <div key={k}>
              <dt>{labels[k]}</dt>
              <dd>
                {k === "categories"
                  ? v.length
                    ? `Выбраны ветки: ${v
                        .map((x) => {
                          try {
                            const a = JSON.parse(String(x));
                            return Array.isArray(a) ? a[1] : x;
                          } catch {
                            return x;
                          }
                        })
                        .join(", ")}; ${row.parent} → ${row.name}`
                    : "Все категории"
                  : v.length
                    ? v.join(", ")
                    : k === "years"
                      ? "Вся история"
                      : "Все"}
              </dd>
            </div>
          ))}
      </dl>
      <h3>Сезонный профиль · {metric === "units" ? "Штуки" : "Выручка"}</h3>
      <p>
        Отклонение от собственного среднемесячного уровня, %. Магазины имеют
        равный вес.
      </p>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={points}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="month" />
          <YAxis unit="%" />
          <Tooltip formatter={(n) => signed(Number(n))} />
          <Line
            dataKey="index"
            name="Индекс"
            stroke="#2674d2"
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
      {p.estimated && (
        <p role="status">
          Ориентировочный профиль: часть месяцев оценена по регулярности записей
          или меняется состав магазинов.
        </p>
      )}
      {!detail && !detailError && (
        <p role="status">Загрузка детализации из снимка…</p>
      )}
      {detailError && (
        <p role="alert">
          {detailError}{" "}
          <button onClick={() => setRetry((r) => r + 1)}>
            Повторить детализацию
          </button>
        </p>
      )}
      <div className="category-season-scroll">
        <table>
          <thead>
            <tr>
              <th>Магазин / год</th>
              {months.map((m) => (
                <th key={m}>{m.slice(0, 3)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((k) => {
              const [store, year] = JSON.parse(k) as [string, number];
              return (
                <tr key={k}>
                  <th scope="row">
                    {store}
                    <small>{year}</small>
                  </th>
                  {months.map((m, i) => {
                    const a = lookup("units", store, year, i + 1),
                      r = lookup("revenue", store, year, i + 1);
                    return (
                      <td key={m}>
                        {a ? (
                          <>
                            <div>{value(a.value)} шт.</div>
                            <div>{value(r?.value ?? 0)} ₽</div>
                            <small>
                              {a.included
                                ? a.method === "estimated"
                                  ? "Ориентировочно"
                                  : "Покрытие подтверждено"
                                : `Неполные: ${a.reason}`}
                            </small>
                          </>
                        ) : (
                          <small>Нет данных</small>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Магазины, годы и причины исключения месяцев</summary>
        {p.cells.map((c, i) => (
          <p key={i}>{tooltip(c, i)}</p>
        ))}
      </details>
    </dialog>
  );
}

export function CategorySeasonalityPage() {
  const [years, setYears] = useState<number[]>([]),
    [stores, setStores] = useState<string[]>([]),
    [categories, setCategories] = useState<string[]>([]),
    [brands, setBrands] = useState<string[]>([]),
    [manufacturers, setManufacturers] = useState<string[]>([]),
    [manager, setManager] = useState<string[]>([]),
    [buyer, setBuyer] = useState<string[]>([]),
    [metric, setMetric] = useState<"units" | "revenue">("units");
  const [data, setData] = useState<Result>(),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0),
    [selected, setSelected] = useState<string>();
  const origin = useRef<HTMLElement | null>(null),
    serial = useRef(0);
  const params = useMemo<Params>(
    () => ({
      years,
      stores,
      categories,
      brands,
      manufacturers,
      manager,
      buyer_type: buyer,
    }),
    [years, stores, categories, brands, manufacturers, manager, buyer],
  );
  useEffect(() => {
    const controller = new AbortController(),
      id = ++serial.current;
    setLoading(true);
    setError("");
    const timer = setTimeout(() => {
      void reportJson<Result>(
        "/reports/category-seasonality",
        controller.signal,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(params),
        },
      )
        .then((result) => {
          if (id === serial.current && !controller.signal.aborted) {
            setData(result);
            setSelected(undefined);
            setLoading(false);
          }
        })
        .catch((e) => {
          if (!controller.signal.aborted && id === serial.current) {
            setError(
              e instanceof Error
                ? e.message
                : "Ошибка загрузки готового отчёта",
            );
            setLoading(false);
          }
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [params, retry]);
  const options = data?.options,
    groups = [...new Set(data?.rows.map((r) => r.parent_key))],
    row = data?.rows.find((r) => r.key === selected);
  function open(key: string) {
    origin.current = document.activeElement as HTMLElement | null;
    setSelected(key);
  }
  return (
    <div className="page category-seasonality-page">
      <div className="page-title">
        <div>
          <h1>Сезонность категорий</h1>
          <p>
            В какие месяцы продажи выше или ниже собственного обычного уровня
          </p>
        </div>
      </div>
      <section className="panel dynamics-filters">
        <div className="report-filters">
          <label>
            Годы расчёта
            <MultiSelect
              options={(options?.years ?? []).map(String)}
              value={years.map(String)}
              onChange={(v) => setYears(v.map(Number))}
              placeholder="Вся история"
              searchable
              showActions
            />
          </label>
          <label>
            Магазин
            <MultiSelect
              options={options?.stores ?? []}
              value={stores}
              onChange={setStores}
              placeholder="Все магазины"
              searchable
              showActions
            />
          </label>
          <div>
            <span>Категории и подкатегории</span>
            <CategoryTreeFilter
              kind="id"
              providedNodes={options?.nodes ?? []}
              label="Категории и подкатегории"
              value={categories}
              onChange={setCategories}
            />
          </div>
          <label>
            Бренд
            <MultiSelect
              options={options?.brand ?? []}
              value={brands}
              onChange={setBrands}
              placeholder="Все бренды"
              searchable
              showActions
            />
          </label>
          <label>
            Производитель
            <MultiSelect
              options={options?.manufacturer ?? []}
              value={manufacturers}
              onChange={setManufacturers}
              placeholder="Все производители"
              searchable
              showActions
            />
          </label>
          <label>
            Вид покупателя
            <MultiSelect
              options={options?.buyer_types ?? []}
              value={buyer}
              onChange={setBuyer}
              placeholder="Все виды покупателей"
              searchable
              showActions
            />
          </label>
          <label>
            Менеджер
            <MultiSelect
              options={options?.managers ?? []}
              value={manager}
              onChange={setManager}
              placeholder="Все менеджеры"
              searchable
              showActions
            />
          </label>
        </div>
      </section>
      {data &&
        Object.keys(params).some(
          (k) =>
            JSON.stringify(params[k as keyof Params]) !==
            JSON.stringify(data.filters[k as keyof Params]),
        ) && (
          <p role="status">
            На карте предыдущий выбор фильтров.{" "}
            {loading
              ? "Обновление из снимка…"
              : "Повторите загрузку после ошибки."}
          </p>
        )}
      {loading && (
        <p role="status">
          Загрузка готового отчёта…{data ? " На экране предыдущий выбор." : ""}
        </p>
      )}
      {error && (
        <section className="panel" role="alert">
          <p>Ошибка быстрого запроса: {error}</p>
          <button onClick={() => setRetry((r) => r + 1)}>
            Повторить загрузку
          </button>
        </section>
      )}
      {data && (
        <section className="panel cs-snapshot-info">
          <p>
            Последнее успешное обновление: <b>{moscow(data.last_success)}</b>{" "}
            МСК. Последний завершённый месяц:{" "}
            <b>
              {data.through
                ? new Date(data.through).toLocaleDateString("ru-RU", {
                    timeZone: "Europe/Moscow",
                    month: "long",
                    year: "numeric",
                  })
                : "—"}
            </b>
            .
          </p>
          <p>
            Следующее плановое обновление: {moscow(data.next_update)} МСК
            (суббота, 19:00).
          </p>
          {data.updating && (
            <p role="status">
              Снимок обновляется. Показаны предыдущие опубликованные данные.
            </p>
          )}
          {data.previous_after_error && (
            <p role="alert">
              Очередное обновление не удалось. Показаны данные предыдущего
              успешного расчёта.
            </p>
          )}
        </section>
      )}
      {data?.state === "uninitialized" && (
        <section className="panel analytics-empty">
          <h2>Отчёт ещё не рассчитан</h2>
          <p>
            Первое заполнение выполняет администратор отдельной командой.
            Посещение страницы не запускает расчёт.
          </p>
        </section>
      )}
      {data?.state === "ready" && (
        <section className="panel cs-map-panel">
          <div className="category-season-toolbar">
            <h2>Тепловая карта спроса</h2>
            <div role="group" aria-label="Метрика">
              {(["units", "revenue"] as const).map((m) => (
                <button
                  key={m}
                  aria-pressed={m === metric}
                  className={m === metric ? "primary" : ""}
                  onClick={() => setMetric(m)}
                >
                  {m === "units" ? "Штуки" : "Выручка"}
                </button>
              ))}
            </div>
          </div>
          <p>{data.warning}</p>
          <div className="category-season-legend">
            <span>
              <i className="above" />
              Выше обычного
            </span>
            <span>
              <i className="cs-below" />
              Ниже обычного
            </span>
            <span>
              <i className="normal" />
              Около нуля
            </span>
            <span>
              <i className="no-data" />
              Нет данных
            </span>
          </div>
          {!data.rows.length ? (
            <p role="status">Нет данных по выбранным фильтрам.</p>
          ) : (
            <div className="category-season-scroll">
              <table className="category-season-heatmap">
                <thead>
                  <tr>
                    <th>Подкатегория / родитель</th>
                    {months.map((m) => (
                      <th key={m}>{m}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => {
                    const members = data.rows.filter((r) => r.parent_key === g);
                    return (
                      <Fragment key={g}>
                        <tr className="category-season-parent">
                          <th colSpan={13} scope="colgroup">
                            {members[0].parent}
                          </th>
                        </tr>
                        {members.map((r) => (
                          <tr key={r.key}>
                            <th scope="row">
                              <small>{r.parent}</small>
                              <button onClick={() => open(r.key)}>
                                {r.name}
                              </button>
                              {r[metric].estimated && (
                                <small>Ориентировочно</small>
                              )}
                              {r[metric].cells.some((c) =>
                                c.notes.includes("База по доступным месяцам"),
                              ) && <small>База по доступным месяцам</small>}
                            </th>
                            {r[metric].cells.map((c, i) => (
                              <td
                                key={i}
                                className={c.index === null ? "no-data" : ""}
                                style={heat(c)}
                              >
                                <button
                                  className="cs-cell"
                                  onClick={() => open(r.key)}
                                  aria-label={`${r.parent} → ${r.name}. ${tooltip(c, i)}`}
                                >
                                  <span>
                                    {c.index === null
                                      ? "Нет данных"
                                      : signed(c.index)}
                                  </span>
                                  {c.years.length === 1 && (
                                    <small>Данные за один год</small>
                                  )}
                                  {r[metric].peaks.includes(i + 1) && (
                                    <small>Пик спроса</small>
                                  )}
                                  {r[metric].troughs.includes(i + 1) && (
                                    <small>Максимальный спад</small>
                                  )}
                                  <span
                                    role="tooltip"
                                    className="cs-cell-tooltip"
                                  >
                                    {tooltip(c, i)}
                                  </span>
                                </button>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <details>
            <summary>Как рассчитаны индексы</summary>
            <p>
              Для каждого магазина усредняются пригодные одноимённые месяцы всех
              лет. База — среднее типичных значений по доступным календарным
              месяцам с равными весами. Индекс = (типичный месяц / база − 1) ×
              100%. Затем усредняются индексы магазинов с равным весом. Это не
              сравнение с другим месяцем или годом. Текущий месяц исключён по
              Москве. При одном календарном месяце или неположительной базе
              индекса нет.
            </p>
            <p>
              Успешные файлы с объявленным периодом подтверждают обработку
              заявленного интервала, но не независимую полноту выгрузки 1С. Без
              них ≥90% дней с записями дают только ориентировочную регулярность.
              Отсутствие подходящих продаж в таком месяце не превращается в
              подтверждённый ноль. Магазины и годы показаны в подсказке, один
              год не доказывает повторяемость.
            </p>
          </details>
        </section>
      )}
      {row && data && (
        <Detail
          key={row.key}
          row={row}
          snapshotId={data.snapshot_id!}
          metric={metric}
          filters={data.filters}
          origin={origin.current}
          onClose={() => setSelected(undefined)}
        />
      )}
    </div>
  );
}
