import { BrandFilter } from "../components/BrandFilter";
import {SaleDrawer} from "../components/SaleDrawer";
import type {Sale} from "../types";
import {ReportActions,ReportEmpty} from '../components/ReportActions';
import {useExplicitReport,reportJson,reportDownload} from '../hooks/useExplicitReport';
import {useMemo,useState} from 'react';
import {Bar,BarChart,CartesianGrid,Cell,Legend,Line,LineChart,Pie,PieChart,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {query} from '../api/client';
import {MultiSelect} from '../components/MultiSelect';
import {Empty,Loading} from '../components/States';
import {useApi} from '../hooks/useApi';
import {date,money,number,percent} from '../utils/format';

type PeriodKind='yesterday'|'current_week'|'previous_week'|'current_month'|'previous_month'|'custom';
type Comparison={current:number;previous:number;difference:number;change_percent:number|null};
type CategoryRow={category:string;current_revenue:number;previous_revenue:number;current_units:number;current_checks:number;current_average_check:number;current_share:number;previous_share:number;share_change_pp:number;revenue_change:number;revenue_change_percent:number|null;contribution_percent:number|null;contribution:string};
type Report={period:{start:string;end:string};previous_period:{start:string;end:string};year_comparison:{categories:CategoryRow[];period:{start:string;end:string};metrics:Record<string,Comparison>;growth_drivers:CategoryRow[];decline_drivers:CategoryRow[]};warning?:string;metrics:Record<string,Comparison>;categories:CategoryRow[];category_options:string[];structure:Array<{category:string;revenue:number;share:number}>;growth_drivers:CategoryRow[];decline_drivers:CategoryRow[]};
type Detail={category:CategoryRow;products:Array<{article?:string;code?:string;name:string;revenue:number;units:number;checks:number;share:number;revenue_change:number}>;group_by:string;points:Array<{period:string;revenue:number;units:number}>};
const COLORS=['#2878ff','#9b6cff','#22a06b','#f59e0b','#e05263','#25a9c8','#6f7b90','#ef7d32','#58a55c','#b05cc7','#aab3c2'];
const iso=(value:Date)=>`${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
function periodDates(kind:PeriodKind){const now=new Date(),from=new Date(now),to=new Date(now),day=(now.getDay()+6)%7;if(kind==='yesterday'){from.setDate(from.getDate()-1);to.setDate(to.getDate()-1)}if(kind==='current_week')from.setDate(from.getDate()-day);if(kind==='previous_week'){from.setDate(from.getDate()-day-7);to.setDate(from.getDate()+6)}if(kind==='current_month')from.setDate(1);if(kind==='previous_month'){from.setMonth(from.getMonth()-1,1);to.setDate(0)}return {from:iso(from),to:iso(to)}}
const periods:Array<[PeriodKind,string]>=[['yesterday','Вчера'],['current_week','Текущая неделя'],['previous_week','Предыдущая неделя'],['current_month','Текущий месяц'],['previous_month','Предыдущий месяц'],['custom','Произвольный период']];
const metricNames:Record<string,string>={revenue:'Выручка',units:'Продано товаров',checks:'Количество чеков',average_check:'Средний чек',categories:'Количество категорий'};
const formatMetric=(key:string,value:number)=>key==='revenue'||key==='average_check'?money(value):number(value);
const signedPercent=(value:number|null)=>value==null?'—':`${value>0?'+':''}${percent(value)}`;
const Memo=({title,children}:{title:string;children:string})=><div className="category-memo"><b>{title}</b><span>{children}</span></div>;

export function CategoryAnalyticsReportPage() {
  const [brands, setBrands] = useState<string[]>([]);
  const initial = periodDates("current_month");
  const [kind, setKind] = useState<PeriodKind>("current_month"),
    [from, setFrom] = useState(initial.from),
    [to, setTo] = useState(initial.to),
    [stores, setStores] = useState<string[]>([]),
    [manager, setManager] = useState<string[]>([]),
    [buyerType, setBuyerType] = useState<string[]>([]),
    [category, setCategory] = useState(""),
    [chartLimit, setChartLimit] = useState("10"),
    [chartComparison, setChartComparison] = useState("previous_period"),
    [selected, setSelected] = useState<string>(),
    [detailGroup, setDetailGroup] = useState(""),
    [growthMode, setGrowthMode] = useState("previous_period"),
    [declineMode, setDeclineMode] = useState("previous_period"),
    [detailMode, setDetailMode] = useState("previous_period"),
    [productSales, setProductSales] = useState<{items:Sale[];page:number;pages:number;total:number}>(),
    [salesProduct, setSalesProduct] = useState<Detail["products"][number]>(),
    [sale, setSale] = useState<Sale>();
  const { data: filters } = useApi<{ departments: string[] }>("/filters"),
    { data: managers } = useApi<string[]>("/integrations/clients-vr/managers"),
    { data: buyerTypes } = useApi<string[]>(
      "/integrations/clients-vr/buyer-types",
    );
  const params = {
    date_from: from,
    date_to: to,
    period_kind: kind,
    stores,
    manager,
    buyer_type: buyerType,
    brands,
    category,
  };
  const controller = useExplicitReport(params, (p, signal) => {setSelected(undefined);setSalesProduct(undefined);setSale(undefined);return reportJson<Report>(`/reports/category-analytics?${query(p)}`, signal)},
  );
  const { data, loading } = controller;
  const [detail, setDetail] = useState<Detail>();
  const detailLoading = controller.operation === "details";
  function openCategory(value: string, group = detailGroup, mode = detailMode) {
    if (!controller.snapshot) return;
    const p = {
      ...controller.snapshot,
      category: undefined,
      group_by: group || undefined,
      comparison_mode: mode,
    };
    void controller.perform(
      "details",
      (signal) =>
        reportJson<Detail>(
          `/reports/category-analytics/${encodeURIComponent(value)}/details?${query(p)}`,
          signal,
        ),
      (valueData) => {
        setSelected(value);
        setDetailGroup(group);
        setDetailMode(mode);
        setDetail(valueData);
      },
    );
  }
  function openProduct(row: Detail["products"][number], page = 1) {
    if (!controller.snapshot) return;
    const p = {...controller.snapshot, code: row.code, article: row.article, name: row.name, page};
    void controller.perform("details", signal => reportJson<{items:Sale[];page:number;pages:number;total:number}>(`/reports/category-analytics/product-sales/list?${query(p)}`, signal), value => {setSalesProduct(row);setProductSales(value)});
  }
  function openSale(id: number) {
    void controller.perform("details", signal => reportJson<Sale>(`/sales/${id}`, signal), setSale);
  }
  const growthRows = growthMode === "previous_year" ? data?.year_comparison.growth_drivers : data?.growth_drivers;
  const declineRows = declineMode === "previous_year" ? data?.year_comparison.decline_drivers : data?.decline_drivers;
  const comparisonButtons = (value: string, change: (mode: string) => void) => <div className="chart-granularity" aria-label="Период сравнения">{[["previous_year", "Предыдущий год"], ["previous_period", "Предыдущий период"]].map(([mode,label]) => <button key={mode} type="button" aria-pressed={value === mode} className={value === mode ? "active" : ""} onClick={() => change(mode)}>{label}</button>)}</div>;
  const compared = useMemo(() => {
    const rows = (chartComparison === "previous_year" ? data?.year_comparison.categories : data?.categories) || [];
    return chartLimit === "all" ? rows : rows.slice(0, Number(chartLimit));
  }, [data, chartLimit, chartComparison]);
  function selectPeriod(value: PeriodKind) {
    setKind(value);
    if (value !== "custom") {
      const range = periodDates(value);
      setFrom(range.from);
      setTo(range.to);
    }
  }
  async function exportExcel() {
    if (controller.snapshot)
      await controller.perform("export", (signal) =>
        reportDownload(
          `/reports/category-analytics/export/xlsx?${query(controller.snapshot!)}`,
          "category-analytics.xlsx",
          signal,
        ),
      );
  }
  return (
    <div className="page category-report">
      <div className="page-title">
        <div>
          <h1>Аналитика категорий</h1>
          <p>Структура продаж и вклад товарных групп в изменение выручки</p>
        </div>
        <button
          className="primary"
          disabled={controller.busy || !controller.snapshot}
          onClick={exportExcel}
        >
          Выгрузить в Excel
        </button>
      </div>
      <section className="panel dynamics-filters">
        <div className="period-picker">
          {periods.map(([value, label]) => (
            <button
              key={value}
              className={kind === value ? "active" : ""}
              onClick={() => selectPeriod(value)}
            >
              {label}
            </button>
          ))}
        </div>
        {kind === "custom" && (
          <div className="custom-dates">
            <label>
              Дата с
              <input
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
              />
            </label>
            <label>
              Дата по
              <input
                type="date"
                min={from}
                value={to}
                onChange={(event) => setTo(event.target.value)}
              />
            </label>
          </div>
        )}
        <div className="report-filters">
          <label>
            Магазин
            <MultiSelect
              options={filters?.departments || []}
              value={stores}
              onChange={setStores}
              placeholder="Все магазины"
            />
          </label>
          <BrandFilter value={brands} onChange={setBrands} />
          <label>
            Менеджер
            <MultiSelect options={managers || []} value={manager} onChange={setManager} placeholder="Все менеджеры" searchable showActions />
          </label>
          <label>
            Вид покупателя
            <MultiSelect options={[...new Set(["Нет", ...(buyerTypes || [])])]} value={buyerType} onChange={setBuyerType} placeholder="Все виды покупателей" searchable showActions />
          </label>
          <label>
            Категория
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <option value="">Все категории</option>
              {data?.category_options.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>
        <Memo title="Как настроить выборку">
          Период задаёт границы анализа, а магазин, менеджер и вид покупателя
          позволяют проверить структуру продаж отдельного сегмента. Фильтр
          категории полезен для углублённого анализа одной товарной группы.
        </Memo>
        <ReportActions report={controller} />
      </section>
      <ReportEmpty show={!data && !loading} />
      {data?.warning && <div className="report-warning">{data.warning}</div>}
      {loading ? (
        <Loading />
      ) : !data ? null : !data.categories.length ? (
        <Empty text="За выбранный период категории не найдены" />
      ) : (
        <>
          <Memo title="Основные показатели">
            Карточки дают быстрый итог по выбранной выборке. Сравнивайте выручку
            со средним чеком и числом чеков: так можно понять, вызвано изменение
            потоком покупателей или стоимостью их покупок.
          </Memo>
          <div className="kpis category-kpis">
            {Object.entries(metricNames).map(([key, label]) => {
              const metric = data.metrics[key];
              return (
                <div className="kpi" key={key}>
                  <span>{label}</span>
                  <strong>{formatMetric(key, metric.current)}</strong>
                  <small>Сравнение с аналогичным периодом в прошлом году: {formatMetric(key, data.year_comparison.metrics[key].previous)} · {signedPercent(data.year_comparison.metrics[key].change_percent)}</small>
                  <small>Сравнение с прошлым периодом: {formatMetric(key, metric.previous)} · {signedPercent(metric.change_percent)}</small>
                </div>
              );
            })}
          </div>
          <section className="panel chart category-structure">
            <h2>Структура выручки по категориям</h2>
            <Memo title="Как читать структуру">
              Диаграмма показывает, какую часть товарной выручки формирует
              каждая категория. Крупные сектора — основа оборота; рост
              зависимости от одной категории повышает риск, а увеличение доли
              перспективной группы показывает изменение спроса.
            </Memo>
            <ResponsiveContainer width="100%" height={340}>
              <PieChart>
                <Pie
                  data={data.structure}
                  dataKey="revenue"
                  nameKey="category"
                  innerRadius={62}
                  outerRadius={115}
                  label={({ name, percent: part }) =>
                    `${name} ${(Number(part) * 100).toFixed(1)}%`
                  }
                >
                  {data.structure.map((_, index) => (
                    <Cell key={index} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(value) => money(Number(value))} />
              </PieChart>
            </ResponsiveContainer>
          </section>
          <section className="panel chart category-revenue-chart">
            <div className="section-title">
              <h2>Выручка по категориям</h2>
              {comparisonButtons(chartComparison, setChartComparison)}
              <select
                value={chartLimit}
                onChange={(event) => setChartLimit(event.target.value)}
              >
                <option value="10">ТОП-10</option>
                <option value="20">ТОП-20</option>
                <option value="all">Все</option>
              </select>
            </div>
            <p className="muted">Сравнение с {chartComparison === "previous_year" ? "аналогичным периодом прошлого года" : "предыдущим периодом"}: {date((chartComparison === "previous_year" ? data.year_comparison.period : data.previous_period).start)} — {date((chartComparison === "previous_year" ? data.year_comparison.period : data.previous_period).end)}</p>
            <Memo title="Сравнение периодов">
              Вертикальные столбцы сопоставляют текущую и прошлую выручку.
              Большой разрыв между столбцами сразу показывает лидеров роста и
              категории, которые требуют проверки ассортимента, наличия или
              продвижения.
            </Memo>
            <div className="category-bars">
              <div
                style={{
                  width: `max(100%, ${Math.max(compared.length * 110, 900)}px)`,
                  height: 400,
                }}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={compared}
                    margin={{ left: 20, right: 20, bottom: 80 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis
                      type="category"
                      dataKey="category"
                      angle={-35}
                      textAnchor="end"
                      interval={0}
                      height={95}
                    />
                    <YAxis type="number" width={95} tickFormatter={number} />
                    <Tooltip formatter={(value) => money(Number(value))} />
                    <Legend verticalAlign="top" itemSorter={(item) => item.dataKey === "previous_revenue" ? 0 : 1} />
                    <Bar
                      dataKey="current_revenue"
                      name="Текущий период"
                      fill="#2878ff"
                    />
                    <Bar
                      dataKey="previous_revenue"
                      name={chartComparison === "previous_year" ? "Предыдущий год" : "Предыдущий период"}
                      fill="#9b6cff"
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </section>
          <div className="category-drivers">
            <section className="panel driver-card">
              <h2>Что обеспечило рост</h2>
              {comparisonButtons(growthMode, setGrowthMode)}
              <Memo title="Драйверы роста">
                Здесь категории с максимальным приростом в рублях. Они
                показывают, какие товарные группы реально обеспечили улучшение
                общего результата и где стоит поддержать наличие и продвижение.
              </Memo>
              {growthRows?.length ? (
                growthRows.map((row) => (
                  <button
                    key={row.category}
                    onClick={() => openCategory(row.category, detailGroup, growthMode)}
                  >
                    <span>{row.category}</span>
                    <b className="trend-up">+{money(row.revenue_change)}</b>
                  </button>
                ))
              ) : (
                <p className="muted">
                  Категорий с ростом за выбранный период нет
                </p>
              )}
            </section>
            <section className="panel driver-card">
              <h2>Категории с падением</h2>
              {comparisonButtons(declineMode, setDeclineMode)}
              <Memo title="Зоны внимания">
                Блок ранжирует отрицательный вклад по сумме потери. Верхние
                позиции сильнее всего тянут выручку вниз — для них полезно
                проверить остатки, цены, сезонность и изменение спроса.
              </Memo>
              {declineRows?.length ? (
                declineRows.map((row) => (
                  <button
                    key={row.category}
                    onClick={() => openCategory(row.category, detailGroup, declineMode)}
                  >
                    <span>{row.category}</span>
                    <b className="trend-down">{money(row.revenue_change)}</b>
                  </button>
                ))
              ) : (
                <p className="muted">
                  Категорий со снижением продаж за выбранный период нет
                </p>
              )}
            </section>
          </div>
          <section className="panel table-panel">
            <div className="section-title">
              <div>
                <h2>Категории</h2>
                <p>
                  {date(data.period.start)} — {date(data.period.end)} ·
                  сравнение с {date(data.previous_period.start)} —{" "}
                  {date(data.previous_period.end)}
                </p>
              </div>
            </div>
            <Memo title="Подробная аналитика">
              Таблица объединяет объём, долю, средний чек и вклад в изменение
              выручки. Рост доли при падении общей выручки не всегда означает
              рост категории, поэтому оценивайте долю вместе с абсолютным
              изменением. Нажмите строку для просмотра товаров и динамики.
            </Memo>
            <div className="table-wrap category-table">
              <table>
                <thead>
                  <tr>
                    <th>Категория</th>
                    <th>Выручка</th>
                    <th>Доля</th>
                    <th>Продано</th>
                    <th>Чеков</th>
                    <th>Средний чек</th>
                    <th>Прошлая выручка</th>
                    <th>Изменение, ₽</th>
                    <th>Изменение, %</th>
                    <th>Доля, п.п.</th>
                    <th>Вклад, %</th>
                  </tr>
                </thead>
                <tbody>
                  {data.categories.map((row) => (
                    <tr
                      key={row.category}
                      onClick={() => openCategory(row.category, detailGroup, "previous_period")}
                    >
                      <td>
                        <b>{row.category}</b>
                      </td>
                      <td>{money(row.current_revenue)}</td>
                      <td>{percent(row.current_share)}</td>
                      <td>{number(row.current_units)}</td>
                      <td>{number(row.current_checks)}</td>
                      <td>{money(row.current_average_check)}</td>
                      <td>{money(row.previous_revenue)}</td>
                      <td
                        className={
                          row.revenue_change >= 0 ? "trend-up" : "trend-down"
                        }
                      >
                        {money(row.revenue_change)}
                      </td>
                      <td>{signedPercent(row.revenue_change_percent)}</td>
                      <td>
                        {row.share_change_pp > 0 ? "+" : ""}
                        {percent(row.share_change_pp)}
                      </td>
                      <td>{signedPercent(row.contribution_percent)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      {selected && !loading && (
        <>
          <div className="backdrop" onClick={() => setSelected(undefined)} />
          <section className="category-detail">
            <div className="drawer-head">
              <div>
                <small>Детализация категории</small>
                <h2>{selected}</h2>
              </div>
              <button onClick={() => setSelected(undefined)}>Закрыть</button>
            </div>
            <div className="drawer-body">
              {controller.error && <p role="alert">{controller.error}</p>}
              {detailLoading ? (
                <Loading />
              ) : (
                detail && (
                  <>
                    <Memo title="Показатели категории">
                      Сводка помогает оценить масштаб категории и её изменение.
                      Сопоставляйте долю, число чеков и средний чек, чтобы
                      понять, зависит результат от охвата покупателей или суммы
                      товаров категории в чеке.
                    </Memo>
                    <div className="kpis category-detail-kpis">
                      {[
                        ["Выручка", money(detail.category.current_revenue)],
                        ["Продано", number(detail.category.current_units)],
                        ["Чеков", number(detail.category.current_checks)],
                        [
                          "Средний чек",
                          money(detail.category.current_average_check),
                        ],
                        ["Доля", percent(detail.category.current_share)],
                        ["Изменение", money(detail.category.revenue_change)],
                      ].map(([label, value]) => (
                        <div className="kpi" key={label}>
                          <span>{label}</span>
                          <strong>{value}</strong>
                        </div>
                      ))}
                    </div>
                    <section className="panel chart">
                      <div className="section-title">
                        <h2>Динамика выручки</h2>
                        <div className="chart-granularity">
                          {["day", "week", "month"].map((value) => (
                            <button
                              key={value}
                              className={
                                (detailGroup || detail.group_by) === value
                                  ? "active"
                                  : ""
                              }
                              disabled={controller.busy}
                              onClick={() =>
                                selected && openCategory(selected, value)
                              }
                            >
                              {value === "day"
                                ? "По дням"
                                : value === "week"
                                  ? "По неделям"
                                  : "По месяцам"}
                            </button>
                          ))}
                        </div>
                      </div>
                      <Memo title="Динамика категории">
                        График показывает устойчивость продаж во времени.
                        Провалы помогают найти дни или недели потери спроса, а
                        длительный рост отличить от разовой крупной продажи.
                      </Memo>
                      <ResponsiveContainer width="100%" height={300}>
                        <LineChart data={detail.points}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="period" tickFormatter={date} />
                          <YAxis width={95} tickFormatter={number} />
                          <Tooltip
                            formatter={(value) => money(Number(value))}
                          />
                          <Line
                            dataKey="revenue"
                            name="Выручка"
                            stroke="#2878ff"
                            strokeWidth={3}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </section>
                    <h3>ТОП товаров категории</h3>
                    <Memo title="Товары внутри категории">
                      Таблица показывает, какие позиции формируют результат
                      категории. Высокая доля одного товара означает зависимость
                      от его наличия; отрицательное изменение помогает найти
                      конкретные источники падения.
                    </Memo>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Артикул</th>
                            <th>Код</th>
                            <th>Наименование</th>
                            <th>Выручка</th>
                            <th>Продано</th>
                            <th>Чеков</th>
                            <th>Доля</th>
                            <th>Изменение</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.products.map((row, index) => (
                            <tr key={`${row.code}-${row.article}-${index}`} tabIndex={0} role="button" aria-label={`Продажи товара ${row.name}`} onClick={() => openProduct(row)} onKeyDown={event => {if (event.key === "Enter" || event.key === " ") {event.preventDefault();openProduct(row)}}}>
                              <td>{row.article || "—"}</td>
                              <td>{row.code || "—"}</td>
                              <td>{row.name}</td>
                              <td>{money(row.revenue)}</td>
                              <td>{number(row.units)}</td>
                              <td>{number(row.checks)}</td>
                              <td>{percent(row.share)}</td>
                              <td
                                className={
                                  row.revenue_change >= 0
                                    ? "trend-up"
                                    : "trend-down"
                                }
                              >
                                {money(row.revenue_change)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )
              )}
            </div>
          </section>
        </>
      )}
      {salesProduct && productSales && <><div className="backdrop" onClick={() => setSalesProduct(undefined)} /><section className="category-detail" role="dialog" aria-modal="true" aria-label="Продажи товара"><div className="drawer-head"><h2>Продажи: {salesProduct.name}</h2><button onClick={() => setSalesProduct(undefined)}>Закрыть</button></div><div className="drawer-body">{controller.busy && <Loading />}{controller.error && <p role="alert">{controller.error}</p>}<p>Продажи за текущий период отчёта. Суммы и скидка относятся ко всему документу.</p><div className="table-wrap"><table><thead><tr>{["Дата","№ документа","Клиент","Подразделение","Сумма","Базовая","Скидка","Автор","Тип цены"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{productSales.items.map(row => <tr key={row.id} tabIndex={0} role="button" aria-label={`Открыть продажу ${row.document_number}`} onClick={() => openSale(row.id)} onKeyDown={event => {if (event.key === "Enter" || event.key === " ") {event.preventDefault();openSale(row.id)}}}><td>{date(row.sale_date)}</td><td>{row.document_number}</td><td>{row.client || "—"}</td><td>{row.department}</td><td>{money(row.total_amount)}</td><td>{money(row.base_amount)}</td><td>{percent(row.discount_percent)}</td><td>{row.author || "—"}</td><td>{row.price_type || "—"}</td></tr>)}</tbody></table></div>{!productSales.items.length && <Empty text="Продажи товара за текущий период не найдены" />}<div className="section-title"><button disabled={controller.busy || productSales.page <= 1} onClick={() => openProduct(salesProduct, productSales.page - 1)}>Назад</button><span>{productSales.page} / {productSales.pages} · Продаж: {productSales.total}</span><button disabled={controller.busy || productSales.page >= productSales.pages} onClick={() => openProduct(salesProduct, productSales.page + 1)}>Далее</button></div></div></section></>}
      {sale && <div className="category-sale-card"><div className="backdrop" onClick={() => setSale(undefined)} /><SaleDrawer sale={sale} onClose={() => setSale(undefined)} /></div>}
    </div>
  );
}
