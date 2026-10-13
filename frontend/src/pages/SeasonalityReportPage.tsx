import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { query } from "../api/client";
import { useApi } from "../hooks/useApi";
import { reportJson, useExplicitReport } from "../hooks/useExplicitReport";
import { ReportActions, ReportEmpty } from "../components/ReportActions";
import { MultiSelect } from "../components/MultiSelect";
import { date, number, percent } from "../utils/format";
import { ErrorBox, Loading } from "../components/States";

type Season = {
  status: string;
  confidence: number;
  reason: string;
  period: string;
};
type Meta = { ready: boolean; last_success?: string; phase: string };
type Product = {
  identity: string;
  product_key: string;
  years: number[];
  seasonality: Season;
};
type Window = {
  start_month: number;
  peak_month: number;
  end_month: number;
  months: number[];
};
type Profile = {
  years: number[];
  indices: Array<number | null>;
  historical_indices: Array<number | null>;
  windows: Window[];
  historical_windows?: Window[];
  confidence: number;
  stability: number;
  trend_percent: number | null;
  annual: Array<{
    year: number;
    eligible: boolean;
    total_units: number | null;
    peak_month: number | null;
    observed_months: number[];
    windows: Window[];
    weight: number;
  }>;
};
type Cursor = { year: number; month: number };
type Detail = {
  product_key: string;
  metadata: Meta;
  profile: Profile | null;
  seasonality: Season;
  display_note: string;
  months: Array<{
    year: number;
    month: number;
    units: string;
    incomplete_source: boolean;
  }>;
  next_cursor: Cursor | null;
};
const monthNames = [
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
  "#9b6cff",
  "#10a877",
  "#e89e22",
  "#e05270",
  "#657b98",
  "#b26626",
  "#4c9ea5",
];
const iso = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
export function SeasonalityReportPage() {
  const [search, setSearch] = useState(""),
    [after, setAfter] = useState<string>(),
    [selected, setSelected] = useState<Product>();
  const [display, setDisplay] = useState("all"),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [store, setStore] = useState("");
  const [years, setYears] = useState<string[]>([]),
    [cursor, setCursor] = useState<Cursor>();
  const report = useExplicitReport({ search, after }, (params, signal) => {
    setSelected(undefined);
    setCursor(undefined);
    return reportJson<{
      items: Product[];
      next_cursor: string | null;
      metadata: Meta;
    }>(`/reports/seasonality?${query(params)}`, signal);
  });
  const { data: filters } = useApi<{ departments: string[] }>("/filters");
  const range = useMemo(() => {
    if (display === "all") return {};
    if (display === "custom")
      return { date_from: from || undefined, date_to: to || undefined };
    const now = new Date(),
      start = new Date(
        now.getFullYear(),
        now.getMonth() - Number(display) + 1,
        1,
      );
    return { date_from: iso(start), date_to: iso(now) };
  }, [display, from, to]);
  const invalid = Boolean(
    range.date_from && range.date_to && range.date_from > range.date_to,
  );
  const path =
    selected && !invalid
      ? `/reports/seasonality/${selected.identity}?${query({ ...range, store, after_year: cursor?.year, after_month: cursor?.month })}`
      : "";
  const { data: detail, loading, error } = useApi<Detail>(path);
  const {
    data: comparison,
    loading: comparing,
    error: comparisonError,
  } = useApi<Detail>(
    selected && years.length && !invalid
      ? `/reports/seasonality/${selected.identity}?${query({ ...range, store, years })}`
      : "",
  );
  const points = useMemo(
    () =>
      Array.from({ length: 12 }, (_, index) => {
        const row: Record<string, string | number | null> = {
          month: monthNames[index],
        };
        for (const year of years) {
          const item = comparison?.months.find(
            (item) => item.year === Number(year) && item.month === index + 1,
          );
          row[year] = item ? Number(item.units) : null;
        }
        return row;
      }),
    [comparison, years],
  );
  function choose(product: Product) {
    setSelected(product);
    setCursor(undefined);
    setYears(product.years.slice(-2).map(String));
  }
  function changeDisplay(value: string) {
    setDisplay(value);
    setCursor(undefined);
  }
  const annualRows =
    detail?.profile?.annual
      .filter((item) => years.includes(String(item.year)))
      .sort((a, b) => a.year - b.year) || [];
  const shift = (current: number | null, previous: number | null) =>
    current == null || previous == null
      ? "—"
      : String(((current - previous + 18) % 12) - 6);
  const annualChange = (index: number) => {
    const item = annualRows[index],
      previous = annualRows[index - 1];
    if (!previous) return "—";
    if (item.observed_months.join(",") !== previous.observed_months.join(","))
      return "Разное покрытие";
    return item.total_units == null ||
      previous.total_units == null ||
      previous.total_units === 0
      ? "—"
      : percent((item.total_units / previous.total_units - 1) * 100);
  };
  const windows = (items: Window[] | undefined) =>
    items?.length
      ? items
          .map(
            (w) =>
              `${monthNames[w.start_month - 1]} — ${monthNames[w.end_month - 1]} (пик: ${monthNames[w.peak_month - 1]})`,
          )
          .join("; ")
      : "Не подтверждены";
  return (
    <div className="page seasonality-report">
      <div className="page-title">
        <div>
          <h1>Сезонность</h1>
          <p>Автоматическая модель по всей доступной истории продаж</p>
        </div>
      </div>
      <section className="panel dynamics-filters">
        <label>
          Поиск по коду или артикулу
          <input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setAfter(undefined);
            }}
            placeholder="Код или артикул"
          />
        </label>
        <ReportActions report={report} />
        <p className="seasonality-note">
          Открытие отчёта только читает готовые показатели. Первоначальный
          расчёт запускается отдельно администратором; период отображения не
          пересчитывает модель.
        </p>
      </section>
      <ReportEmpty show={!report.data && !report.loading} />
      {report.data && (
        <>
          <p className="seasonality-note">
            {report.data.metadata.ready
              ? "Расчёт актуален"
              : "Актуальный расчёт не завершён; сезонные выводы недоступны"}
            . Последний успешный расчёт:{" "}
            {report.data.metadata.last_success
              ? date(report.data.metadata.last_success)
              : "—"}
          </p>
          <section className="panel table-panel">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Код / артикул</th>
                    <th>Сезонность</th>
                    <th>Уверенность</th>
                    <th>История</th>
                  </tr>
                </thead>
                <tbody>
                  {report.data.items.map((product) => (
                    <tr
                      key={product.identity}
                      className="clickable-row"
                      onClick={() => choose(product)}
                    >
                      <td>
                        <button onClick={() => choose(product)}>
                          {product.product_key.replace(
                            /^(code|article|unknown):/,
                            "",
                          )}
                        </button>
                      </td>
                      <td>{product.seasonality.status}</td>
                      <td>{product.seasonality.confidence} / 100</td>
                      <td>
                        {product.years.length
                          ? `${Math.min(...product.years)}–${Math.max(...product.years)}`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!report.data.items.length && (
              <p>
                Готовые товары не найдены. Проверьте состояние фонового расчёта.
              </p>
            )}
            <div className="analytics-build-actions">
              <button
                disabled={report.busy || !after}
                onClick={() => {
                  setAfter(undefined);
                  void report.generate({
                    search: report.snapshot?.search ?? search,
                    after: undefined,
                  });
                }}
              >
                К началу списка
              </button>
              <button
                disabled={report.busy || !report.data.next_cursor}
                onClick={() => {
                  const next = report.data!.next_cursor!;
                  setAfter(next);
                  void report.generate({
                    search: report.snapshot?.search ?? search,
                    after: next,
                  });
                }}
              >
                Следующая страница
              </button>
            </div>
          </section>
        </>
      )}
      {selected && (
        <section className="panel chart">
          <h2>
            История товара:{" "}
            {selected.product_key.replace(/^(code|article|unknown):/, "")}
          </h2>
          <div className="report-filters">
            <label>
              Период отображения
              <select
                value={display}
                onChange={(event) => changeDisplay(event.target.value)}
              >
                <option value="all">Вся история</option>
                <option value="12">Последние 12 месяцев</option>
                <option value="24">Последние 24 месяца</option>
                <option value="36">Последние 36 месяцев</option>
                <option value="custom">Произвольный период</option>
              </select>
            </label>
            {display === "custom" && (
              <>
                <label>
                  Дата с
                  <input
                    type="date"
                    value={from}
                    onChange={(event) => {
                      setFrom(event.target.value);
                      setCursor(undefined);
                    }}
                  />
                </label>
                <label>
                  Дата по
                  <input
                    type="date"
                    value={to}
                    onChange={(event) => {
                      setTo(event.target.value);
                      setCursor(undefined);
                    }}
                  />
                </label>
              </>
            )}
            <label>
              Магазин (только отображение)
              <select
                value={store}
                onChange={(event) => {
                  setStore(event.target.value);
                  setCursor(undefined);
                }}
              >
                <option value="">Все магазины</option>
                {filters?.departments.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
          </div>
          {invalid && (
            <ErrorBox text="Дата начала не может быть позже даты окончания" />
          )}
          {error && <ErrorBox text={error} />}
          {loading ? (
            <Loading />
          ) : (
            !invalid &&
            !error &&
            detail && (
              <>
                <p className="seasonality-note">
                  {detail.display_note}{" "}
                  {!detail.metadata.ready &&
                    "Показаны агрегаты предыдущего завершённого поколения; они могут быть устаревшими. Актуальная модель пока недоступна."}
                </p>
                <p>
                  <b>{detail.seasonality.status}</b> · Уверенность:{" "}
                  {detail.seasonality.confidence} / 100
                </p>
                <p>{detail.seasonality.reason}</p>
                {detail.profile && (
                  <>
                    <div className="kpis seasonality-kpis">
                      <div className="kpi">
                        <span>Актуальные сезонные окна</span>
                        <strong>{windows(detail.profile.windows)}</strong>
                      </div>
                      <div className="kpi">
                        <span>Исторические окна (все годы равновесно)</span>
                        <strong>
                          {windows(detail.profile.historical_windows)}
                        </strong>
                      </div>
                      <div className="kpi">
                        <span>Согласованность сезонных профилей</span>
                        <strong>{detail.profile.stability} / 100</strong>
                      </div>
                      <div className="kpi">
                        <span>Долгосрочный тренд, в год</span>
                        <strong>
                          {detail.profile.trend_percent == null
                            ? "—"
                            : percent(detail.profile.trend_percent)}
                        </strong>
                      </div>
                    </div>
                    <h3>Исторический и актуальный сезонные индексы</h3>
                    <ResponsiveContainer width="100%" height={260}>
                      <LineChart
                        data={monthNames.map((month, index) => ({
                          month,
                          current: detail.profile!.indices[index],
                          historical: detail.profile!.historical_indices[index],
                        }))}
                      >
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="month" />
                        <YAxis />
                        <Tooltip />
                        <Legend />
                        <Line
                          dataKey="current"
                          name="С учётом актуальности"
                          stroke="#2878ff"
                          connectNulls={false}
                        />
                        <Line
                          dataKey="historical"
                          name="Исторический профиль"
                          stroke="#9b6cff"
                          connectNulls={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                    <h3>Сравнение лет</h3>
                    <p className="seasonality-note">
                      До восьми лет одновременно; доступны все годы истории.
                      Разрывы — неизвестные наблюдения, не доказанный нулевой
                      спрос. Окна отдельного года описательные, а не
                      подтверждённая сезонность.
                    </p>
                    <MultiSelect
                      options={detail.profile.years.map(String)}
                      value={years}
                      onChange={(values) => {
                        if (values.length <= 8) setYears(values);
                      }}
                      placeholder="Выберите годы"
                    />
                    {comparisonError && <ErrorBox text={comparisonError} />}
                    {comparing ? (
                      <Loading />
                    ) : comparisonError ? null : (
                      <ResponsiveContainer width="100%" height={320}>
                        <LineChart data={points}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="month" />
                          <YAxis tickFormatter={number} />
                          <Tooltip />
                          <Legend />
                          {years.map((year, index) => (
                            <Line
                              key={year}
                              dataKey={year}
                              name={year}
                              stroke={colors[index]}
                              connectNulls={false}
                            />
                          ))}
                        </LineChart>
                      </ResponsiveContainer>
                    )}
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Год</th>
                            <th>Количество, шт.</th>
                            <th>Пик</th>
                            <th>Окна повышенных продаж / длительность</th>
                            <th>Изменение объёма</th>
                            <th>Сдвиг пика, мес.</th>
                            <th>Сдвиг начала, мес.</th>
                            <th>Изменение длительности, мес.</th>
                            <th>Вес</th>
                            <th>Сопоставимость</th>
                          </tr>
                        </thead>
                        <tbody>
                          {annualRows.map((item, index) => (
                            <tr key={item.year}>
                              <td>{item.year}</td>
                              <td>
                                {item.total_units == null
                                  ? "—"
                                  : number(item.total_units)}
                              </td>
                              <td>
                                {item.peak_month
                                  ? monthNames[item.peak_month - 1]
                                  : "—"}
                              </td>
                              <td>
                                {windows(item.windows)}
                                {item.windows.map((w, i) => (
                                  <span key={i}> · {w.months.length} мес.</span>
                                ))}
                              </td>
                              <td>{annualChange(index)}</td>
                              <td>
                                {shift(
                                  item.peak_month,
                                  annualRows[index - 1]?.peak_month ?? null,
                                )}
                              </td>
                              <td>
                                {item.windows.length === 1 &&
                                annualRows[index - 1]?.windows.length === 1
                                  ? shift(
                                      item.windows[0].start_month,
                                      annualRows[index - 1].windows[0]
                                        .start_month,
                                    )
                                  : "—"}
                              </td>
                              <td>
                                {item.windows.length === 1 &&
                                annualRows[index - 1]?.windows.length === 1
                                  ? item.windows[0].months.length -
                                    annualRows[index - 1].windows[0].months
                                      .length
                                  : "—"}
                              </td>
                              <td>{number(item.weight)}</td>
                              <td>
                                {item.eligible
                                  ? "Пригоден для сравнения"
                                  : "Недостаточно / неполные данные"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="seasonality-note">
                      Изменения сравниваются с предыдущим выбранным годом;
                      сдвиги месяцев циклические. При разном покрытии изменение
                      общего объёма не вычисляется. Для нескольких окон начало и
                      длительность сравнивайте по перечню окон. Таблица годовых
                      показателей и модель относятся ко всем магазинам и всей
                      истории; диаграмма и месячные строки учитывают выбранные
                      фильтры отображения.
                    </p>
                  </>
                )}
                <h3>Помесячные продажи — {store || "Все магазины"}</h3>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Год</th>
                        <th>Месяц</th>
                        <th>Количество, шт.</th>
                        <th>Источник</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.months.map((item) => (
                        <tr key={`${item.year}-${item.month}`}>
                          <td>{item.year}</td>
                          <td>{monthNames[item.month - 1]}</td>
                          <td>{item.units}</td>
                          <td>
                            {item.incomplete_source
                              ? "Неполный / ошибочный импорт"
                              : "Загруженные продажи"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!detail.months.length && (
                  <p>В выбранном диапазоне агрегаты отсутствуют.</p>
                )}
                <button disabled={!cursor} onClick={() => setCursor(undefined)}>
                  К началу истории
                </button>{" "}
                <button
                  disabled={!detail.next_cursor}
                  onClick={() => setCursor(detail.next_cursor!)}
                >
                  Следующие месяцы
                </button>
              </>
            )
          )}
        </section>
      )}
    </div>
  );
}
