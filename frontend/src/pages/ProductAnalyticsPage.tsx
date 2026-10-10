import { useEffect, useState } from "react";
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
import { API, query } from "../api/client";
import { MultiSelect } from "../components/MultiSelect";
import { PeriodPicker, type Period } from "../components/PeriodPicker";
import { Empty, ErrorBox, Loading } from "../components/States";
import { useApi } from "../hooks/useApi";
import { useAnalyticsRequest } from "../hooks/useAnalyticsRequest";
import { date, money, number, percent } from "../utils/format";
type Tab = "top" | "growth" | "decline" | "stopped" | "new";
type Row = {
  key: string;
  article?: string;
  code?: string;
  name: string;
  brand: string;
  manufacturer: string;
  category: string;
  catalog_status?: string;
  subcategory: string;
  material: string;
  image_url?: string;
  properties?: unknown;
  stocks?: unknown;
  prices?: unknown;
  revenue: number;
  previous_revenue: number;
  revenue_difference: number;
  revenue_change: number | null;
  units: number;
  previous_units: number;
  units_difference: number;
  checks: number;
  previous_checks: number;
  checks_change: number | null;
  average_price: number;
  first_sale?: string;
  last_sale?: string;
  sku?: number;
};
type Summary = {
  revenue: { current: number; previous: number; change_percent: number | null };
  units: { current: number; previous: number; change_percent: number | null };
  checks: { current: number; previous: number };
  sku: { current: number; previous: number };
  average_revenue_per_sku: number;
  average_units_per_sku: number;
  new: number;
  growth: number;
  decline: number;
  stopped: number;
};
type Parameters = {
  date_from: string;
  date_to: string;
  compare_from?: string;
  compare_to?: string;
  departments: string[];
  managers: string[];
  brands: string[];
  manufacturers: string[];
  subcategories: string[];
  group_by: string;
  search: string;
  article: string;
};
type ReportRequest = {
  parameters: Parameters;
  tab: Tab;
  sort: string;
  limit: number;
};
type Report = {
  catalog?: { warning: string | null; missing: number; counts: Record<string, number> }; summary: Summary; items: Row[]; total: number };
type Detail = {
  product: Row | null;
  points: Array<{ period: string; revenue: number; units: number }>;
};
async function analyticsResponse(response: Response) {
  if (response.ok) return response;
  if (response.status === 503)
    throw new Error(
      "Сервер занят или недостаточно памяти для отчёта. Подождите и повторите запрос вручную.",
    );
  throw new Error(
    await response
      .json()
      .then((body) =>
        typeof body.detail === "string"
          ? body.detail
          : `Ошибка ${response.status}`,
      )
      .catch(() => `Ошибка ${response.status}`),
  );
}
function detailDates(period: "week" | "month" | "quarter" | "year") {
  const end = new Date(),
    start = new Date(end);
  if (period === "week") start.setDate(end.getDate() - 6);
  else if (period === "month") start.setMonth(end.getMonth() - 1);
  else if (period === "quarter") start.setMonth(end.getMonth() - 3);
  else start.setFullYear(end.getFullYear() - 1);
  return {
    date_from: iso(start),
    date_to: iso(end),
    group_by:
      period === "quarter" ? "week" : period === "year" ? "month" : "day",
  };
}
const iso = (d: Date) => d.toISOString().slice(0, 10);
const shiftMonth = (value: string) => {
  const source = new Date(`${value}T12:00:00`),
    day = source.getDate(),
    target = new Date(source.getFullYear(), source.getMonth() - 1, 1, 12);
  target.setDate(
    Math.min(
      day,
      new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate(),
    ),
  );
  return iso(target);
};
const shiftYear = (value: string) => {
  const source = new Date(`${value}T12:00:00`),
    target = new Date(source);
  target.setFullYear(source.getFullYear() - 1);
  if (target.getMonth() !== source.getMonth()) target.setDate(0);
  return iso(target);
};
const now = new Date(),
  monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
const tabNames: Record<Tab, string> = {
  top: "ТОП",
  growth: "Рост",
  decline: "Падение",
  stopped: "Перестали продаваться",
  new: "Новые товары",
};
const tabHelp: Record<Tab, { title: string; text: string; tip: string }> = {
  top: {
    title: "Как использовать ТОП",
    text: "Показывает товары или группы, которые формируют основной объем продаж. Переключайте сортировку по выручке, количеству или числу чеков, чтобы отличить дорогие позиции от часто покупаемых.",
    tip: "Сосредоточьтесь на лидерах с отрицательной динамикой: их снижение сильнее всего влияет на общий результат.",
  },
  growth: {
    title: "Как трактовать рост",
    text: "Здесь находятся позиции, которые продавались в обоих периодах и увеличили выручку. Изменение рассчитано относительно выбранного периода сравнения.",
    tip: "Большой прирост в рублях показывает вклад в бизнес, а высокий процент — быстро растущие позиции с небольшой исходной базой.",
  },
  decline: {
    title: "Как трактовать падение",
    text: "Показывает позиции, которые продавались в обоих периодах, но потеряли выручку. Вверху находятся товары с наиболее заметным абсолютным снижением.",
    tip: "Проверьте остатки, цену, выкладку и доступность лидеров падения: именно они создают наибольший риск для выручки.",
  },
  stopped: {
    title: "Почему товары перестали продаваться",
    text: "В этот раздел попадают позиции с продажами в периоде сравнения и без продаж в основном периоде.",
    tip: "Высокая прошлая выручка может указывать на отсутствие товара, вывод из ассортимента или проблему с заменой артикула.",
  },
  new: {
    title: "Как оценивать новые товары",
    text: "Показывает позиции без продаж в периоде сравнения, которые начали продаваться в основном периоде. Процент роста для них намеренно не рассчитывается.",
    tip: "Сортировка по выручке помогает быстро найти успешные запуски и позиции, которым стоит обеспечить наличие и продвижение.",
  },
};
const productKinds: Record<string, string> = {
  "1": "Обычный",
  "10": "Акция месяца",
  "11": "Разукомплектация",
  "12": "Минимальная наценка",
  "13": "Акция розница",
  "14": "Первая цена",
  "15": "9-19",
  "2": "Ограниченная скидка",
  "3": "Надо продать",
  "4": "Куплен по акции",
  "5": "Прочие акции",
  "6": "Дисконт",
  "7": "Товар с деффектом",
  "8": "Последний экземпляр",
  "9": "Сетевой",
};
const productKindValue = (name: string, value: string) =>
  name.trim().toLocaleLowerCase("ru-RU") === "вид товара"
    ? productKinds[value.trim()] || value
    : value;
const groupNames = {
  product: "Товару / артикулу",
  brand: "Бренду",
  manufacturer: "Производителю",
  category: "Категории",
  subcategory: "Подкатегории",
  material: "Материалу",
};
export function ProductAnalyticsPage() {
  const [dateFrom, setDateFrom] = useState(iso(monthStart)),
    [dateTo, setDateTo] = useState(iso(now)),
    [period, setPeriod] = useState<Period>("month"),
    [compareFrom, setCompareFrom] = useState(shiftYear(iso(monthStart))),
    [compareTo, setCompareTo] = useState(shiftYear(iso(now))),
    [manualCompare, setManualCompare] = useState(true),
    [comparePreset, setComparePreset] = useState<"year" | "month" | null>(
      "year",
    ),
    [departments, setDepartments] = useState<string[]>([]),
    [groupBy, setGroupBy] = useState("product"),
    [tab, setTab] = useState<Tab>("top"),
    [topSort, setTopSort] = useState("revenue"),
    [limit, setLimit] = useState(20),
    [search, setSearch] = useState(""),
    [debouncedName, setDebouncedName] = useState(""),
    [article, setArticle] = useState(""),
    [brands, setBrands] = useState<string[]>([]),
    [brandSearch, setBrandSearch] = useState(""),
    [debouncedBrandSearch, setDebouncedBrandSearch] = useState(""),
    [manufacturers, setManufacturers] = useState<string[]>([]),
    [manufacturerSearch, setManufacturerSearch] = useState(""),
    [debouncedManufacturerSearch, setDebouncedManufacturerSearch] =
      useState(""),
    [managers, setManagers] = useState<string[]>([]),
    [subcategories, setSubcategories] = useState<string[]>([]),
    [subcategorySearch, setSubcategorySearch] = useState(""),
    [debouncedSubcategorySearch, setDebouncedSubcategorySearch] = useState(""),
    [selected, setSelected] = useState<Row>(),
    [detailPeriod, setDetailPeriod] = useState<
      "week" | "month" | "quarter" | "year"
    >("month");
  const { data: filters } = useApi<{ departments: string[] }>("/filters");
  const { data: managerOptions } = useApi<string[]>(
    "/reports/product-analytics/manager-options",
  );
  useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedSubcategorySearch(subcategorySearch),
      300,
    );
    return () => clearTimeout(timer);
  }, [subcategorySearch]);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedBrandSearch(brandSearch), 300);
    return () => clearTimeout(timer);
  }, [brandSearch]);
  useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedManufacturerSearch(manufacturerSearch),
      300,
    );
    return () => clearTimeout(timer);
  }, [manufacturerSearch]);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedName(search), 300);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    if (comparePreset === "year") {
      setCompareFrom(shiftYear(dateFrom));
      setCompareTo(shiftYear(dateTo));
    } else if (comparePreset === "month") {
      setCompareFrom(shiftMonth(dateFrom));
      setCompareTo(shiftMonth(dateTo));
    }
  }, [dateFrom, dateTo, comparePreset]);
  const { data: catalogState } = useApi<{ enabled?: boolean; has_unscanned_sales?: boolean; lookup_counts?: Record<string, number>; state: { completed: boolean; last_success_at: string | null; last_full_success_at: string | null } | null }>("/reports/product-analytics/catalog-status");
  const { data: subcategoryOptions } = useApi<string[]>(
    `/reports/product-analytics/catalog-options/subcategory?${query({ search: debouncedSubcategorySearch })}`,
  );
  const { data: brandOptions } = useApi<string[]>(
    `/reports/product-analytics/catalog-options/brand?${query({ search: debouncedBrandSearch })}`,
  );
  const { data: manufacturerOptions } = useApi<string[]>(
    `/reports/product-analytics/catalog-options/manufacturer?${query({ search: debouncedManufacturerSearch })}`,
  );
  const { data: nameSuggestions } = useApi<string[]>(
    debouncedName.trim().length >= 4
      ? `/reports/product-analytics/name-suggestions?${query({ search: debouncedName })}`
      : "",
  );
  const payload = {
    date_from: dateFrom,
    date_to: dateTo,
    compare_from: manualCompare ? compareFrom : undefined,
    compare_to: manualCompare ? compareTo : undefined,
    departments,
    managers,
    brands,
    manufacturers,
    subcategories,
    group_by: groupBy,
    search,
    article,
  };
  const request = useAnalyticsRequest();
  const [attempt, setAttempt] = useState<ReportRequest>(),
    [displayed, setDisplayed] = useState<ReportRequest>(),
    [reportData, setReportData] = useState<Report>(),
    [error, setError] = useState(""),
    [exportError, setExportError] = useState(""),
    [detail, setDetail] = useState<Detail>(),
    [detailError, setDetailError] = useState(""),
    [detailRange, setDetailRange] = useState(() => detailDates("month"));
  const loading = request.operation === "report",
    table = reportData,
    summaryData = reportData;
  const filtersChanged = Boolean(
    displayed &&
      JSON.stringify(payload) !== JSON.stringify(displayed.parameters),
  );
  const viewChanged = Boolean(
    displayed &&
      (tab !== displayed.tab ||
        limit !== displayed.limit ||
        (tab === "top" && topSort !== displayed.sort)),
  );
  async function generate(snapshot: ReportRequest) {
    const result = await request.run("report", async (signal) => {
      setAttempt(snapshot);
      setError("");
      setExportError("");
      setSelected(undefined);
      setDetail(undefined);
      const endpoint = `/reports/product-analytics/report?${query({ section: snapshot.tab, sort_by: snapshot.tab === "top" ? snapshot.sort : undefined, limit: snapshot.limit })}`;
      const response = await analyticsResponse(
        await fetch(`${API}${endpoint}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(snapshot.parameters),
          signal,
        }),
      );
      return response.json() as Promise<Report>;
    });
    if (result?.ok) {
      setReportData(result.data);
      setDisplayed(snapshot);
    } else if (result) setError(result.error);
  }
  function generateDraft() {
    void generate({
      parameters: JSON.parse(JSON.stringify(payload)),
      tab,
      sort: topSort,
      limit,
    });
  }
  function updateView() {
    if (displayed) void generate({ ...displayed, tab, sort: topSort, limit });
  }
  async function loadDetail(row: Row, period: typeof detailPeriod) {
    if (!displayed) return;
    const range = detailDates(period);
    const result = await request.run("details", async (signal) => {
      setSelected(row);
      setDetailPeriod(period);
      setDetailRange(range);
      setDetail(undefined);
      setDetailError("");
      const body = JSON.stringify({
        ...displayed.parameters,
        date_from: range.date_from,
        date_to: range.date_to,
        compare_from: undefined,
        compare_to: undefined,
        article_key: row.key,
      });
      const response = await analyticsResponse(
        await fetch(
          `${API}/reports/product-analytics/details?group_by=${range.group_by}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            signal,
          },
        ),
      );
      return response.json() as Promise<Detail>;
    });
    if (result?.ok) setDetail(result.data);
    else if (result) setDetailError(result.error);
  }
  const rows = table?.items || [];
  async function exportExcel() {
    if (!displayed) return;
    const result = await request.run("export", async (signal) => {
      setExportError("");
      const response = await analyticsResponse(
        await fetch(`${API}/reports/product-analytics/export`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(displayed.parameters),
          signal,
        }),
      );
      const blob = await response.blob();
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const url = URL.createObjectURL(blob),
        a = document.createElement("a");
      try {
        a.href = url;
        a.download = "product-analytics.xlsx";
        a.click();
      } finally {
        URL.revokeObjectURL(url);
      }
    });
    if (result && !result.ok) setExportError(result.error);
  }
  const s = summaryData?.summary;
  const product = detail?.product || selected;
  const entries = (value: unknown) => {
    if (Array.isArray(value))
      return value.map((item, index) => {
        if (typeof item !== "object" || !item)
          return { name: `Значение ${index + 1}`, value: String(item) };
        const row = item as Record<string, unknown>,
          name = String(
            row.name ||
              row.property ||
              row.warehouse ||
              row.stock ||
              row.price_type ||
              `Значение ${index + 1}`,
          ),
          raw = String(
            row.value ?? row.quantity ?? row.stock_quantity ?? row.price ?? "—",
          );
        return { name, value: productKindValue(name, raw) };
      });
    if (value && typeof value === "object")
      return Object.entries(value as Record<string, unknown>).map(
        ([name, item]) => ({
          name,
          value: productKindValue(name, String(item ?? "—")),
        }),
      );
    return [];
  };
  const cards = s
    ? ([
        ["Выручка", money(s.revenue.current), s.revenue.change_percent],
        ["Продано единиц", number(s.units.current), s.units.change_percent],
        ["Количество чеков", number(s.checks.current), null],
        ["Продававшихся SKU", number(s.sku.current), null],
        ["Выручка на SKU", money(s.average_revenue_per_sku), null],
        ["Единиц на SKU", number(s.average_units_per_sku), null],
        ["Новые товары", number(s.new), null],
        ["Товары с ростом", number(s.growth), null],
        ["Товары с падением", number(s.decline), null],
        ["Без продаж", number(s.stopped), null],
      ] as const)
    : [];
  return (
    <div className="page product-analytics">
      <div className="page-title">
        <div>
          <h1>Товарная аналитика</h1>
          <p>Лидеры, рост, падение и новые товарные позиции</p>
        </div>
        <button
          className="primary"
          disabled={request.busy || !displayed}
          onClick={exportExcel}
        >
          {request.operation === "export"
            ? "Формируется Excel…"
            : "Выгрузить в Excel"}
        </button>
      </div>
      {catalogState && catalogState.enabled !== false && (!catalogState.state?.last_full_success_at || !catalogState.state.completed || catalogState.has_unscanned_sales) && (
        <div className="report-warning" role="status">
          Локальные характеристики товаров ещё не сверены полностью. Некоторые значения фильтров и характеристики могут отсутствовать. Обратитесь к администратору для ручной синхронизации.
        </div>
      )}
      {catalogState?.state?.last_success_at && <p className="muted">Последняя успешная синхронизация характеристик: {new Date(catalogState.state.last_success_at).toLocaleString("ru-RU")}</p>}
      {reportData?.catalog?.warning && <div className="report-warning" role="status">{reportData.catalog.warning}</div>}
      <section className="panel report-builder">
        <div className="analytics-periods">
          <fieldset>
            <legend>Основной период</legend>
            <PeriodPicker
              showDay={false}
              showAll={false}
              value={{ period, date_from: dateFrom, date_to: dateTo }}
              onChange={(value) => {
                setPeriod(value.period);
                setDateFrom(value.date_from);
                setDateTo(value.date_to);
              }}
            />
          </fieldset>
          <fieldset>
            <legend>
              <label>
                <input
                  type="checkbox"
                  checked={manualCompare}
                  onChange={(e) => {
                    setManualCompare(e.target.checked);
                    if (!e.target.checked) setComparePreset(null);
                  }}
                />{" "}
                Период сравнения вручную
              </label>
            </legend>
            <div className="comparison-quick">
              <button
                type="button"
                className={comparePreset === "year" ? "active" : ""}
                onClick={() => {
                  setManualCompare(true);
                  setComparePreset("year");
                  setCompareFrom(shiftYear(dateFrom));
                  setCompareTo(shiftYear(dateTo));
                }}
              >
                Прошлый год
              </button>
              <button
                type="button"
                className={comparePreset === "month" ? "active" : ""}
                onClick={() => {
                  setManualCompare(true);
                  setComparePreset("month");
                  setCompareFrom(shiftMonth(dateFrom));
                  setCompareTo(shiftMonth(dateTo));
                }}
              >
                Прошлый месяц
              </button>
            </div>
            <input
              type="date"
              disabled={!manualCompare}
              value={compareFrom}
              onChange={(e) => {
                setComparePreset(null);
                setCompareFrom(e.target.value);
              }}
            />
            <input
              type="date"
              disabled={!manualCompare}
              value={compareTo}
              onChange={(e) => {
                setComparePreset(null);
                setCompareTo(e.target.value);
              }}
            />
          </fieldset>
        </div>
        <div className="report-filters">
          <label>
            Магазин
            <MultiSelect
              options={filters?.departments || []}
              value={departments}
              onChange={setDepartments}
              placeholder="Все магазины"
            />
          </label>
          <label>
            Артикул / код
            <input
              value={article}
              onChange={(e) => setArticle(e.target.value)}
            />
          </label>
          <label>
            Название
            <input
              list="product-name-suggestions"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <datalist id="product-name-suggestions">
              {nameSuggestions?.map((value) => (
                <option key={value} value={value} />
              ))}
            </datalist>
          </label>
          <label>
            Бренд
            <MultiSelect
              options={[...new Set([...brands, ...(brandOptions || [])])]}
              value={brands}
              onChange={setBrands}
              placeholder="Все бренды"
              searchable
              showActions
              search={brandSearch}
              onSearch={setBrandSearch}
              emptyText="Бренды не найдены"
            />
          </label>
          <label>
            Производитель
            <MultiSelect
              options={[
                ...new Set([...manufacturers, ...(manufacturerOptions || [])]),
              ]}
              value={manufacturers}
              onChange={setManufacturers}
              placeholder="Все производители"
              searchable
              showActions
              search={manufacturerSearch}
              onSearch={setManufacturerSearch}
              emptyText="Производители не найдены"
            />
          </label>
          <label>
            Менеджер
            <MultiSelect
              options={managerOptions || []}
              value={managers}
              onChange={setManagers}
              placeholder="Все менеджеры"
              searchable
              showActions
              emptyText="Менеджеры не найдены"
            />
          </label>
          <label>
            Подкатегория
            <MultiSelect
              options={[
                ...new Set([...subcategories, ...(subcategoryOptions || [])]),
              ]}
              value={subcategories}
              onChange={setSubcategories}
              placeholder="Все подкатегории"
              searchable
              showActions
              search={subcategorySearch}
              onSearch={setSubcategorySearch}
              emptyText="Подкатегории не найдены"
            />
          </label>
          <label>
            Группировать по
            <select
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value)}
            >
              {Object.entries(groupNames).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="analytics-build-actions">
          <button
            className="primary"
            disabled={request.busy}
            onClick={generateDraft}
          >
            {loading ? "Формируется отчёт…" : "Сформировать отчёт"}
          </button>
          <span role="status">
            {filtersChanged
              ? "Фильтры изменены. Нажмите «Сформировать отчёт», чтобы применить их."
              : displayed
                ? "Показаны результаты сформированного отчёта."
                : "Выберите параметры и сформируйте отчёт."}
          </span>
          {request.busy && (
            <button onClick={request.cancel}>Прекратить ожидание</button>
          )}
        </div>
      </section>
      {exportError && <ErrorBox text={exportError} />}
      <p className="muted analytics-export-note">
        Excel выгружается по параметрам сформированного отчёта.
      </p>
      {error && (
        <div className="analytics-request-error" role="alert">
          <ErrorBox text={error} />
          <button
            disabled={request.busy || !attempt}
            onClick={() => attempt && void generate(attempt)}
          >
            Повторить запрос
          </button>
        </div>
      )}
      {loading && <Loading />}
      {!reportData && !loading && !error && (
        <section className="panel analytics-empty">
          <h2>Сформируйте товарный отчёт</h2>
          <p>Выберите параметры и нажмите «Сформировать отчёт».</p>
        </section>
      )}
      {reportData && (
        <>
          <div className="kpis product-analytics-kpis">
            {cards.map(([label, value, change]) => (
              <div className="kpi" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
                {change != null && (
                  <small className={change >= 0 ? "up" : "down"}>
                    {change >= 0 ? "+" : ""}
                    {percent(change)}
                  </small>
                )}
              </div>
            ))}
          </div>
          <section className="panel">
            <div className="tabs">
              {(Object.keys(tabNames) as Tab[]).map((x) => (
                <button
                  key={x}
                  className={tab === x ? "active" : ""}
                  onClick={() => setTab(x)}
                >
                  {tabNames[x]}
                </button>
              ))}
            </div>
            <div className="analytics-help">
              <div>
                <b>{tabHelp[tab].title}</b>
                <p>{tabHelp[tab].text}</p>
              </div>
              <small>
                <strong>На что обратить внимание:</strong> {tabHelp[tab].tip}
              </small>
            </div>
            <div className="table-meta">
              <div>
                {tab === "top" && (
                  <select
                    value={topSort}
                    onChange={(e) => setTopSort(e.target.value)}
                  >
                    <option value="revenue">По выручке</option>
                    <option value="units">По количеству</option>
                    <option value="checks">По числу чеков</option>
                  </select>
                )}
              </div>
              <label>
                Показывать{" "}
                <select
                  value={limit}
                  onChange={(e) => setLimit(+e.target.value)}
                >
                  {[10, 20, 50, 100].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
              <button disabled={request.busy} onClick={updateView}>
                Обновить результаты
              </button>
            </div>
            <div className="analytics-result-status" role="status">
              {displayed &&
                `Показан раздел «${tabNames[displayed.tab]}», до ${displayed.limit} строк.`}
              {viewChanged &&
                " Вкладка, сортировка или число строк изменены. Нажмите «Обновить результаты». Обновление использует параметры сформированного отчёта."}
            </div>
            {!rows.length ? (
              <Empty text="Данные не найдены" />
            ) : (
              <div className="table-wrap product-analytics-table">
                <table>
                  <thead>
                    <tr>
                      <th>№</th>
                      <th>Фото</th>
                      <th>Артикул</th>
                      <th>Товар / группа</th>
                      <th>Прошлая выручка</th>
                      <th>Выручка</th>
                      <th>Изменение</th>
                      <th>Количество</th>
                      <th>Чеков</th>
                      <th>Средняя цена</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((x, i) => (
                      <tr
                        key={x.key}
                        onClick={() =>
                          !request.busy &&
                          displayed?.parameters.group_by === "product" &&
                          void loadDetail(x, detailPeriod)
                        }
                      >
                        <td>{i + 1}</td>
                        <td>
                          <ProductPhoto src={x.image_url} name={x.name} />
                        </td>
                        <td>{x.article || x.code || "—"}</td>
                        <td>{x.name}{x.catalog_status && x.catalog_status !== "matched" && <small>{({ not_synced: "Характеристики не синхронизированы", pending: "Характеристики не синхронизированы", ambiguous: "Неоднозначное сопоставление", not_found: "Товар не найден в VRCatalog", invalid: "Нет корректных идентификаторов" } as Record<string, string>)[x.catalog_status] || "Характеристики отсутствуют"}</small>}</td>
                        <td>{money(x.previous_revenue)}</td>
                        <td>{money(x.revenue)}</td>
                        <td
                          className={
                            (x.revenue_difference || 0) >= 0
                              ? "trend-up"
                              : "trend-down"
                          }
                        >
                          {money(x.revenue_difference)}
                          <small>
                            {x.revenue_change == null
                              ? "Новый товар"
                              : percent(x.revenue_change)}
                          </small>
                        </td>
                        <td>{number(x.units)}</td>
                        <td>{number(x.checks)}</td>
                        <td>{money(x.average_price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
      {selected && (
        <>
          <div className="backdrop" onClick={() => setSelected(undefined)} />
          <section className="product-detail">
            <div className="drawer-head">
              <div>
                <small>Карточка товара</small>
                <h2>{selected.name}</h2>
              </div>
              <button onClick={() => setSelected(undefined)}>Закрыть</button>
            </div>
            <div className="drawer-body">
              <div className="product-card-overview">
                <ProductPhoto
                  src={product?.image_url}
                  name={product?.name || "Товар"}
                  large
                />
                <div className="field-grid">
                  {[
                    ["Артикул", product?.article],
                    ["Код", product?.code],
                    ["Бренд", product?.brand],
                    ["Производитель", product?.manufacturer],
                    ["Категория", product?.category],
                    ["Материал", product?.material],
                    [
                      "Первая продажа",
                      product?.first_sale
                        ? date(product.first_sale)
                        : undefined,
                    ],
                    [
                      "Последняя продажа",
                      product?.last_sale ? date(product.last_sale) : undefined,
                    ],
                  ].map(([label, value]) => (
                    <div className="field" key={label}>
                      <span>{label}</span>
                      <b>{value || "Не заполнено"}</b>
                    </div>
                  ))}
                </div>
              </div>
              <div className="kpis">
                <div className="kpi">
                  <span>Выручка</span>
                  <strong>{money(product?.revenue || 0)}</strong>
                </div>
                <div className="kpi">
                  <span>Количество</span>
                  <strong>{number(product?.units || 0)}</strong>
                </div>
                <div className="kpi">
                  <span>Чеки</span>
                  <strong>{number(product?.checks || 0)}</strong>
                </div>
                <div className="kpi">
                  <span>Средняя цена</span>
                  <strong>{money(product?.average_price || 0)}</strong>
                </div>
              </div>
              <div className="catalog-card-sections">
                <section>
                  <h3>Остатки по складам</h3>
                  {entries(product?.stocks).length ? (
                    <div className="catalog-values">
                      {entries(product?.stocks).map((x, i) => (
                        <div key={`${x.name}-${i}`}>
                          <span>{x.name}</span>
                          <b>{x.value}</b>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="muted">
                      Данные об остатках не предоставлены CatalogVR
                    </p>
                  )}
                </section>
                <section>
                  <h3>Цены</h3>
                  {entries(product?.prices).length ? (
                    <div className="catalog-values">
                      {entries(product?.prices).map((x, i) => (
                        <div key={`${x.name}-${i}`}>
                          <span>{x.name}</span>
                          <b>{x.value}</b>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="muted">
                      Данные о ценах не предоставлены CatalogVR
                    </p>
                  )}
                </section>
                <section>
                  <h3>Характеристики</h3>
                  {entries(product?.properties).length ? (
                    <div className="catalog-values">
                      {entries(product?.properties).map((x, i) => (
                        <div key={`${x.name}-${i}`}>
                          <span>{x.name}</span>
                          <b>{x.value}</b>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="muted">
                      Характеристики не предоставлены CatalogVR
                    </p>
                  )}
                </section>
              </div>
              <div className="product-detail-chart-head">
                <div>
                  <h3>Динамика продаж</h3>
                  <p>
                    {date(detailRange.date_from)} — {date(detailRange.date_to)}{" "}
                    ·{" "}
                    {detailRange.group_by === "day"
                      ? "по дням"
                      : detailRange.group_by === "week"
                        ? "по неделям"
                        : "по месяцам"}
                  </p>
                </div>
                <div
                  className="chart-granularity"
                  role="group"
                  aria-label="Период графика"
                >
                  {(
                    [
                      ["week", "Неделя"],
                      ["month", "Месяц"],
                      ["quarter", "Квартал"],
                      ["year", "Год"],
                    ] as const
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      className={detailPeriod === key ? "active" : ""}
                      disabled={request.busy}
                      onClick={() => selected && void loadDetail(selected, key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {request.operation === "details" && (
                <>
                  <Loading />
                  <button onClick={request.cancel}>
                    Прекратить ожидание карточки
                  </button>
                </>
              )}
              {detailError && (
                <div role="alert">
                  <ErrorBox text={detailError} />
                  <button
                    disabled={request.busy}
                    onClick={() =>
                      selected && void loadDetail(selected, detailPeriod)
                    }
                  >
                    Повторить загрузку карточки
                  </button>
                </div>
              )}
              <ResponsiveContainer width="100%" height={330}>
                <LineChart data={detail?.points || []}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" tickFormatter={date} />
                  <YAxis yAxisId="money" width={100} tickFormatter={number} />
                  <YAxis yAxisId="units" orientation="right" />
                  <Tooltip />
                  <Legend />
                  <Line
                    yAxisId="money"
                    dataKey="revenue"
                    name="Выручка"
                    stroke="#2878ff"
                  />
                  <Line
                    yAxisId="units"
                    dataKey="units"
                    name="Количество"
                    stroke="#9b6cff"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function ProductPhoto({
  src,
  name,
  large = false,
}: {
  src?: string;
  name: string;
  large?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const className = large ? "product-card-photo" : "analytics-product-photo";
  return !src || failed ? (
    <span className={`${className} analytics-photo-placeholder`}>Нет фото</span>
  ) : (
    <img
      className={className}
      src={src}
      alt={name}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}
