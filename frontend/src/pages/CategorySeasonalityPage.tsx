import { Fragment, useEffect, useRef, useState } from "react";
import {
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import {
  CategoryTreeFilter,
  type CategoryNode,
} from "../components/CategoryTreeFilter";
import { MultiSelect } from "../components/MultiSelect";
import { ReportActions, ReportEmpty } from "../components/ReportActions";
import { useApi } from "../hooks/useApi";
import { useExplicitReport, reportJson } from "../hooks/useExplicitReport";

type Annual = {
  year: number;
  values: number[];
  indices: number[] | null;
  included: boolean;
  reason: string | null;
  coverage: boolean[];
};
type Profile = {
  indices: number[] | null;
  years: number[];
  annual: Annual[];
  peaks: { month: number; count: number; total: number; repeating: boolean }[];
  message: string | null;
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
type Result = {
  rows: Row[];
  years: number[];
  filters: Params;
  warning: string;
  coverage: Record<string, boolean[]>;
  category_labels?: string[];
};
type Options = {
  years: number[];
  nodes: CategoryNode[];
  brand: string[];
  manufacturer: string[];
};
const months = [
  "Янв",
  "Фев",
  "Мар",
  "Апр",
  "Май",
  "Июн",
  "Июл",
  "Авг",
  "Сен",
  "Окт",
  "Ноя",
  "Дек",
];
const fullMonths = [
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
const colors = [
  "#2878ff",
  "#9864c8",
  "#dc8c26",
  "#1d9a96",
  "#cc5771",
  "#698334",
];
const signed = (n: number | null | undefined) =>
  n == null
    ? "Нет данных"
    : `${n > 0 ? "+" : ""}${n.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
const amount = (n: number) =>
  n.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
const heat = (n: number | null | undefined) =>
  n == null ? "no-data" : n > 5 ? "above" : n < -5 ? "below" : "normal";

function Detail({
  row,
  metric,
  filters,
  onClose,
  returnFocus,
  categoryLabels,
}: {
  row: Row;
  metric: "units" | "revenue";
  filters: Params;
  onClose: () => void;
  returnFocus: HTMLElement | null;
  categoryLabels?: string[];
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const origin = returnFocus;
    dialog.current?.showModal();
    return () => {
      dialog.current?.close();
      origin?.focus();
    };
  }, []);
  const p = row[metric],
    points = months.map((month, i) =>
      Object.fromEntries([
        ["month", month],
        ["Типичный профиль", p.indices?.[i] ?? null],
        ...p.annual
          .filter((a) => a.included)
          .map((a) => [String(a.year), a.indices?.[i] ?? null]),
      ]),
    );
  const labels: Record<string, string> = {
    years: "Годы",
    stores: "Магазины",
    categories: "Ветки каталога",
    brands: "Бренд",
    manufacturers: "Производитель",
    manager: "Менеджер",
    buyer_type: "Вид покупателя",
  };
  return (
    <dialog
      ref={dialog}
      className="category-season-detail"
      aria-labelledby="category-season-detail-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div className="category-season-detail-head">
        <div>
          <small>{row.parent}</small>
          <h2 id="category-season-detail-title">{row.name}</h2>
        </div>
        <button autoFocus onClick={onClose}>
          Закрыть
        </button>
      </div>
      <p>Фильтры сформированного отчёта:</p>
      <dl className="category-season-filters">
        {Object.entries(filters).map(([k, v]) => (
          <div key={k}>
            <dt>{labels[k]}</dt>
            <dd>
              {k === "categories"
                ? v.length
                  ? categoryLabels?.join(", ") ||
                    `${v.length} выбранных веток; открыта ${row.parent} → ${row.name}`
                  : "Все категории"
                : v.length
                  ? v.join(", ")
                  : "Все"}
            </dd>
          </div>
        ))}
      </dl>
      <h3>
        Отклонение от среднемесячного уровня, % ·{" "}
        {metric === "units" ? "Штуки" : "Выручка"}
      </h3>
      {p.indices ? (
        <div className="category-season-chart">
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={points}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="month" />
              <YAxis unit="%" />
              <Tooltip formatter={(v) => signed(Number(v))} />
              <Legend />
              {p.annual
                .filter((a) => a.included)
                .map((a, i) => (
                  <Line
                    key={a.year}
                    dataKey={String(a.year)}
                    stroke={colors[i % colors.length]}
                    dot={false}
                  />
                ))}
              <Line
                dataKey="Типичный профиль"
                stroke="#152a46"
                strokeWidth={3}
                connectNulls={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p role="status">Нет пригодных лет: сезонный профиль не рассчитан.</p>
      )}
      {p.message && <p role="status">{p.message}</p>}
      <h3>Фактические продажи по месяцам</h3>
      <p>
        «Нет данных» означает неподтверждённое покрытие. Отдельные
        зарегистрированные продажи показаны в скобках и не включаются в профиль.
      </p>
      <div className="category-season-scroll">
        <table>
          <thead>
            <tr>
              <th>Год / показатель</th>
              {months.map((m) => (
                <th key={m}>{m}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {p.annual.map((a) => (
              <tr key={a.year}>
                <th scope="row">
                  {a.year}
                  <small>{a.included ? "В профиле" : a.reason}</small>
                </th>
                {months.map((m, i) => (
                  <td key={m}>
                    <div>
                      {a.coverage[i]
                        ? amount(
                            row.units.annual.find((v) => v.year === a.year)!
                              .values[i],
                          )
                        : `Нет данных (${amount(row.units.annual.find((v) => v.year === a.year)!.values[i])})`}{" "}
                      шт.
                    </div>
                    <div>
                      {a.coverage[i]
                        ? amount(
                            row.revenue.annual.find((v) => v.year === a.year)!
                              .values[i],
                          )
                        : `Нет данных (${amount(row.revenue.annual.find((v) => v.year === a.year)!.values[i])})`}{" "}
                      ₽
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </dialog>
  );
}

export function CategorySeasonalityPage() {
  const {
    data: options,
    error: optionsError,
    loading: optionsLoading,
  } = useApi<Options>("/reports/category-seasonality/options");
  const { data: filters } = useApi<{ departments: string[] }>("/filters");
  const { data: managers, error: managersError } = useApi<string[]>(
    "/integrations/clients-vr/managers",
  );
  const { data: buyers, error: buyersError } = useApi<string[]>(
    "/integrations/clients-vr/buyer-types",
  );
  const [years, setYears] = useState<number[] | null>(null),
    [stores, setStores] = useState<string[]>([]),
    [categories, setCategories] = useState<string[]>([]),
    [brands, setBrands] = useState<string[]>([]),
    [manufacturers, setManufacturers] = useState<string[]>([]),
    [manager, setManager] = useState<string[]>([]),
    [buyer, setBuyer] = useState<string[]>([]),
    [metric, setMetric] = useState<"units" | "revenue">("units"),
    [selected, setSelected] = useState<string>();
  const detailOrigin = useRef<HTMLElement | null>(null);
  function openDetail(key: string) {
    detailOrigin.current = document.activeElement as HTMLElement | null;
    setSelected(key);
  }
  const params: Params = {
    years: years ?? options?.years.slice(0, 3) ?? [],
    stores,
    categories,
    brands,
    manufacturers,
    manager,
    buyer_type: buyer,
  };
  const report = useExplicitReport(params, (p, signal) =>
    reportJson<Result>("/reports/category-seasonality", signal, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }),
  );
  const row = report.data?.rows.find((r) => r.key === selected);
  const groups = [...new Set(report.data?.rows.map((r) => r.parent_key))];
  return (
    <div className="page category-seasonality-page">
      <div className="page-title">
        <div>
          <h1>Сезонность категорий</h1>
          <p>Типичный спрос по месяцам · подкатегории второго уровня</p>
        </div>
      </div>
      <section className="panel dynamics-filters">
        <div className="report-filters">
          <label>
            Годы расчёта
            <MultiSelect
              options={(options?.years ?? []).map(String)}
              value={params.years.map(String)}
              onChange={(v) => setYears(v.map(Number))}
              placeholder="Выберите завершённые годы"
              searchable
              showActions
            />
          </label>
          <label>
            Магазин
            <MultiSelect
              options={filters?.departments ?? []}
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
              options={buyers ?? []}
              value={buyer}
              onChange={setBuyer}
              placeholder="Все виды покупателей"
              searchable
              showActions
            />
            {buyersError && (
              <small role="alert">
                Справочник видов покупателей недоступен
              </small>
            )}
          </label>
          <label>
            Менеджер
            <MultiSelect
              options={managers ?? []}
              value={manager}
              onChange={setManager}
              placeholder="Все менеджеры"
              searchable
              showActions
            />
            {managersError && (
              <small role="alert">Справочник менеджеров недоступен</small>
            )}
          </label>
        </div>
        {optionsLoading && <p role="status">Загрузка справочников…</p>}
        {optionsError && (
          <p role="alert">Не удалось загрузить справочники: {optionsError}</p>
        )}
        <ReportActions report={report} />
      </section>
      <ReportEmpty show={!report.data && !report.busy} />
      {report.data && (
        <section className="panel">
          <div className="category-season-toolbar">
            <h2>Тепловая карта спроса</h2>
            <div role="group" aria-label="Метрика">
              {(["units", "revenue"] as const).map((m) => (
                <button
                  key={m}
                  aria-pressed={metric === m}
                  className={metric === m ? "primary" : ""}
                  onClick={() => setMetric(m)}
                >
                  {m === "units" ? "Штуки" : "Выручка"}
                </button>
              ))}
            </div>
          </div>
          <p>
            Годы: {report.data.years.join(", ")}. Значения — отклонение от
            среднемесячного уровня внутри года, а не рост к соседнему месяцу.
          </p>
          <p className="hint">{report.data.warning}</p>
          <div className="category-season-legend">
            {[
              ["above", "Выше обычного"],
              ["below", "Ниже обычного"],
              ["normal", "Около обычного (±5%)"],
              ["no-data", "Нет данных"],
            ].map(([c, t]) => (
              <span key={c}>
                <i className={c} />
                {t}
              </span>
            ))}
          </div>
          {!report.data.rows.length ? (
            <p role="status">Нет продаж по выбранным фильтрам.</p>
          ) : (
            <div className="category-season-scroll">
              <table className="category-season-heatmap">
                <caption className="category-season-caption">
                  Сезонный профиль подкатегорий; нажмите на название или месяц
                  для детализации
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Подкатегория / родитель</th>
                    {months.map((m) => (
                      <th scope="col" key={m}>
                        {m}
                      </th>
                    ))}
                    <th scope="col">Пик и повторяемость</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => {
                    const members = report.data!.rows.filter(
                      (r) => r.parent_key === g,
                    );
                    return (
                      <Fragment key={g}>
                        <tr className="category-season-parent">
                          <th colSpan={14} scope="colgroup">
                            {members[0].parent}
                          </th>
                        </tr>
                        {members.map((r, i) => {
                          const p = r[metric];
                          return (
                            <tr
                              key={r.key}
                              className={
                                i === 0 ? "category-season-group-start" : ""
                              }
                            >
                              <th scope="row">
                                <small>{r.parent}</small>
                                <button onClick={() => openDetail(r.key)}>
                                  {r.name}
                                </button>
                                <small>
                                  В профиле:{" "}
                                  {p.years.join(", ") || "нет пригодных лет"}
                                </small>
                              </th>
                              {months.map((m, j) => (
                                <td key={m} className={heat(p.indices?.[j])}>
                                  <button
                                    onClick={() => openDetail(r.key)}
                                    aria-label={`${r.parent} → ${r.name}, ${fullMonths[j]}: ${signed(p.indices?.[j])}; лет: ${p.years.length}`}
                                    title={`${r.name} · ${fullMonths[j]} · ${signed(p.indices?.[j])} · ${p.years.length} лет`}
                                  >
                                    {p.indices ? signed(p.indices[j]) : "—"}
                                  </button>
                                </td>
                              ))}
                              <td>
                                {p.peaks.map((k) => (
                                  <div key={k.month}>
                                    {months[k.month - 1]} · {k.count} из{" "}
                                    {k.total} лет
                                    {k.repeating
                                      ? " · повторяется"
                                      : " · не подтверждён"}
                                  </div>
                                ))}
                                {p.message && <small>{p.message}</small>}
                              </td>
                            </tr>
                          );
                        })}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <details>
            <summary>Как читать отчёт</summary>
            <p>
              Для каждого полного года: (месяц / среднее за 12 месяцев − 1) ×
              100%. Типичный профиль — медиана годовых индексов. Годы с нулевой
              или отрицательной базой исключены. Пик — максимум не ниже +20%;
              повторяющийся пик достигает +20% минимум в двух годах и не менее
              чем в двух третях пригодных лет. При одном годе повторяемость не
              оценивается. Цвет дополняет число, отсутствие данных не означает
              ноль.
            </p>
          </details>
        </section>
      )}
      {row && report.snapshot && (
        <Detail
          key={`${row.key}:${metric}`}
          row={row}
          returnFocus={detailOrigin.current}
          categoryLabels={report.data?.category_labels}
          metric={metric}
          filters={report.snapshot}
          onClose={() => setSelected(undefined)}
        />
      )}
    </div>
  );
}
