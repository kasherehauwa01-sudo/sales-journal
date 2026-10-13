import {ReportActions,ReportEmpty} from '../components/ReportActions';
import {useExplicitReport,reportJson,reportDownload} from '../hooks/useExplicitReport';
import {useMemo,useState} from 'react';
import {Bar,BarChart,CartesianGrid,Legend,Line,LineChart,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {api,query} from '../api/client';
import {MultiSelect} from '../components/MultiSelect';
import {SaleDrawer} from '../components/SaleDrawer';
import {Empty,Loading} from '../components/States';
import {useApi} from '../hooks/useApi';
import type {Sale} from '../types';
import {date,money,number,percent} from '../utils/format';

type PeriodKind='today'|'yesterday'|'current_week'|'previous_week'|'current_month'|'previous_month'|'year'|'custom';type Metric='revenue'|'checks'|'average_check'|'items'|'items_per_check'|'average_discount';type Comparison={current:number;previous:number;difference:number;change_percent:number|null};type StoreRow={store:string;metrics:Record<Metric,Comparison>};type StoreComparison={period:{start:string;end:string};metrics:Record<Metric,Comparison>;stores:StoreRow[]};type Report={comparisons?:{previous_period:StoreComparison;previous_year:StoreComparison};period:{start:string;end:string};previous_period:{start:string;end:string};warning?:string;metrics:Record<Metric,Comparison>;stores:StoreRow[]};type DetailSale={id:number;sale_date:string;document_number:string;client?:string;total_amount:number;items:number;discount:number};
const iso=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;function dates(kind:PeriodKind){const now=new Date(),from=new Date(now),to=new Date(now),day=(now.getDay()+6)%7;if(kind==='yesterday'){from.setDate(from.getDate()-1);to.setDate(to.getDate()-1)}if(kind==='current_week')from.setDate(from.getDate()-day);if(kind==='previous_week'){from.setDate(from.getDate()-day-7);to.setDate(from.getDate()+6)}if(kind==='year')from.setMonth(0,1);if(kind==='current_month')from.setDate(1);if(kind==='previous_month'){from.setMonth(from.getMonth()-1,1);to.setDate(0)}return {from:iso(from),to:iso(to)}}
const periods:Array<[PeriodKind,string]>=[['today','Сегодня'],['yesterday','Вчера'],['current_week','Текущая неделя'],['previous_week','Предыдущая неделя'],['current_month','Текущий месяц'],['previous_month','Предыдущий месяц'],['year','Год'],['custom','Произвольный период']];const labels:Record<Metric,string>={revenue:'Выручка',checks:'Количество чеков',average_check:'Средний чек',items:'Продано товаров',items_per_check:'Товаров в чеке',average_discount:'Средняя скидка'};const format=(key:Metric,value:number)=>key==='revenue'||key==='average_check'?money(value):key==='average_discount'?percent(value):number(value);const change=(value:number|null)=>value==null?'—':`${value>0?'+':''}${percent(value)} ${value>0?'↑':value<0?'↓':'→'}`;
export function StoreAnalyticsReportPage() {
  const initial = dates("current_month");
  const [periodKind, setPeriodKind] = useState<PeriodKind>("current_month");
  const [dateFrom, setDateFrom] = useState(initial.from);
  const [dateTo, setDateTo] = useState(initial.to);
  const [stores, setStores] = useState<string[]>([]);
  const [manager, setManager] = useState("");
  const [buyerType, setBuyerType] = useState("");
  const [showPrevious,setShowPrevious]=useState(true),[showYear,setShowYear]=useState(true);
  const [sort, setSort] = useState("revenue");
  const [descending, setDescending] = useState(true);
  const [selected, setSelected] = useState<StoreRow>();
  const [group, setGroup] = useState("");
  const [detailMetric, setDetailMetric] = useState<
    "revenue" | "checks" | "average_check"
  >("revenue");
  const [sale, setSale] = useState<Sale>();
  const { data: filterOptions } = useApi<{ departments: string[] }>("/filters");
  const { data: managers } = useApi<string[]>(
    "/integrations/clients-vr/managers",
  );
  const { data: buyerTypes } = useApi<string[]>(
    "/integrations/clients-vr/buyer-types",
  );
  const params = {
    date_from: dateFrom,
    date_to: dateTo,
    period_kind: periodKind,
    stores,
    manager,
    buyer_type: buyerType,
    include_both: true,
  };
  const controller = useExplicitReport(params, (p, signal) => {setSelected(undefined);return reportJson<Report>(`/reports/store-analytics?${query(p)}`, signal)},
  );
  const { data, loading } = controller;
  const [dynamics, setDynamics] = useState<{
    group_by: string;
    points: Array<Record<string, string | number>>;
  }>();
  const [sales, setSales] = useState<{ items: DetailSale[] }>();
  function openStore(row: StoreRow, selectedGroup = group) {
    if (!controller.snapshot || !data) return;
    const detailParams = query({
      date_from: data.period.start,
      date_to: data.period.end,
      manager: controller.snapshot.manager,
      buyer_type: controller.snapshot.buyer_type,
      group_by: selectedGroup || undefined,
    });
    void controller.perform(
      "details",
      async (signal) => {
        const base = `/reports/store-analytics/stores/${encodeURIComponent(row.store)}`;
        const dynamics = await reportJson<dynamicsState>(
          `${base}/dynamics?${detailParams}`,
          signal,
        );
        const sales = await reportJson<{ items: DetailSale[] }>(
          `${base}/sales?${detailParams}`,
          signal,
        );
        return { dynamics, sales };
      },
      (result) => {
        setSelected(row);
        setGroup(selectedGroup);
        setDynamics(result.dynamics);
        setSales(result.sales);
      },
    );
  }
  type dynamicsState = {
    group_by: string;
    points: Array<Record<string, string | number>>;
  };
  function selectPeriod(kind: PeriodKind) {
    setPeriodKind(kind);
    setGroup("");
    if (kind !== "custom") {
      const value = dates(kind);
      setDateFrom(value.from);
      setDateTo(value.to);
    }
  }
  function sortBy(key: string) {
    if (sort === key) setDescending((x) => !x);
    else {
      setSort(key);
      setDescending(true);
    }
  }
  const visibleComparisons = [
    ...(showPrevious ? [{label:"Предыдущий период", value:data?.comparisons?.previous_period || (data ? {period:data.previous_period,metrics:data.metrics,stores:data.stores} : undefined)}] : []),
    ...(showYear ? [{label:"Прошлый год",value:data?.comparisons?.previous_year}] : []),
  ].filter(item=>item.value);
  const comparisonsForStore=(store:string,key:Metric)=>visibleComparisons.map(item=>({label:item.label,metric:item.value?.stores.find(row=>row.store===store)?.metrics[key]}));
  const rows = useMemo(
    () =>
      [...(data ? Array.from(new Map([...(data.comparisons?.previous_year.stores || []), ...(data.stores || [])].map(row=>[row.store,row])).values()) : [])].sort((a, b) => {
        const changeSort = sort.endsWith("_change"),
          metric = sort.replace("_change", "") as Metric,
          av =
            sort === "store"
              ? a.store
              : changeSort
                ? (data?.comparisons?.[showPrevious ? "previous_period" : "previous_year"].stores.find(row=>row.store===a.store)?.metrics[metric]?.change_percent ?? a.metrics[metric]?.change_percent ?? -Infinity)
                : a.metrics[metric]?.current || 0,
          bv =
            sort === "store"
              ? b.store
              : changeSort
                ? (data?.comparisons?.[showPrevious ? "previous_period" : "previous_year"].stores.find(row=>row.store===b.store)?.metrics[metric]?.change_percent ?? b.metrics[metric]?.change_percent ?? -Infinity)
                : b.metrics[metric]?.current || 0;
        return (
          (typeof av === "string"
            ? av.localeCompare(String(bv), "ru")
            : Number(av) - Number(bv)) * (descending ? -1 : 1)
        );
      }),
    [data, sort, descending, showPrevious],
  );
  async function openSale(id: number) {
    try {
      setSale(await api<Sale>(`/sales/${id}`));
    } catch {}
  }
  async function exportExcel() {
    if (controller.snapshot)
      await controller.perform("export", (signal) =>
        reportDownload(
          `/reports/store-analytics/export?${query(controller.snapshot!)}`,
          "store-analytics.xlsx",
          signal,
        ),
      );
  }
  return (
    <div className="page store-report">
      <div className="page-title">
        <div>
          <h1>Аналитика по магазинам</h1>
          <p>Отчеты · сравнение эффективности подразделений</p>
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
          {periods.map(([key, label]) => (
            <button
              key={key}
              className={periodKind === key ? "active" : ""}
              onClick={() => selectPeriod(key)}
            >
              {label}
            </button>
          ))}
        </div>
        {periodKind === "custom" && (
          <div className="custom-dates">
            <label>
              Дата с
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </label>
            <label>
              Дата по
              <input
                type="date"
                min={dateFrom}
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </label>
          </div>
        )}
        <div className="report-filters">
          <label>
            Магазин
            <MultiSelect
              options={filterOptions?.departments || []}
              value={stores}
              onChange={setStores}
              placeholder="Все магазины"
            />
          </label>
          <label>
            Менеджер
            <select
              value={manager}
              onChange={(e) => setManager(e.target.value)}
            >
              <option value="">Все менеджеры</option>
              {managers?.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            Вид покупателя
            <select
              value={buyerType}
              onChange={(e) => setBuyerType(e.target.value)}
            >
              <option value="">Все виды покупателей</option>
              <option value="Нет">Нет</option>
              {buyerTypes
                ?.filter((x) => x.toLocaleLowerCase("ru") !== "нет")
                .map((x) => (
                  <option key={x}>{x}</option>
                ))}
            </select>
          </label>
        </div>
        <ReportActions report={controller} />
        <div className="dynamics-comparison-checkboxes" role="group" aria-label="Отображать сравнения">
          <label><input type="checkbox" checked={showPrevious} onChange={e=>setShowPrevious(e.target.checked)}/> Предыдущий период</label>
          <label><input type="checkbox" checked={showYear} onChange={e=>setShowYear(e.target.checked)}/> Прошлый год</label>
        </div>
      </section>
      <ReportEmpty show={!data && !loading} />
      {data?.warning && <div className="report-warning">{data.warning}</div>}
      {loading ? (
        <Loading />
      ) : !data ? null : (
        <>
          <div className="kpis store-kpis">
            {(Object.keys(labels) as Metric[]).map((key) => {
              const item = data.metrics[key];
              return (
                <div className="kpi" key={key}>
                  <span>{labels[key]}</span>
                  <strong>{format(key, item.current)}</strong>
                  {visibleComparisons.map(({label,value})=><small key={label} className={(value!.metrics[key].change_percent || 0)>=0?"up":"down"}>{label}: {format(key,value!.metrics[key].previous)} · {change(value!.metrics[key].change_percent)}</small>)}
                </div>
              );
            })}
          </div>
          {!rows.length ? (
            <Empty text="За выбранный период продажи не найдены" />
          ) : (
            <>
              <section className="panel chart">
                <div className="section-title">
                  <div>
                    <h2>Выручка по магазинам</h2>
                    <p>
                      {date(data.period.start)} — {date(data.period.end)}
                      {visibleComparisons.map(item=><span key={item.label}> · {item.label}: {date(item.value!.period.start)} — {date(item.value!.period.end)}</span>)}
                    </p>
                  </div>
                </div>
                <div className="store-chart">
                  <ResponsiveContainer
                    width="100%"
                    height={360}
                  >
                    <BarChart
                      data={rows.map((x) => ({
                        store: x.store,
                        current: x.metrics.revenue.current,
                        previous: data.comparisons?.previous_period.stores.find(row=>row.store===x.store)?.metrics.revenue.previous ?? (data.comparisons ? 0 : x.metrics.revenue.previous),
                        year: data.comparisons?.previous_year.stores.find(row=>row.store===x.store)?.metrics.revenue.previous ?? 0,
                      }))}
                      layout="horizontal"
                      margin={{ left: 24, right: 24 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis type="category" dataKey="store" interval={0} angle={-25} textAnchor="end" height={85} />
                      <YAxis type="number" tickFormatter={number} width={95} />
                      <Tooltip formatter={(v) => money(Number(v))} />
                      <Legend />
                      <Bar
                        dataKey="current"
                        name="Текущий период"
                        fill="#2878ff"
                      />
                      {showPrevious && <Bar
                        dataKey="previous"
                        name="Предыдущий период"
                        fill="#9b6cff"
                      />}
                      {showYear && <Bar dataKey="year" name="Прошлый год" fill="#10a877" />}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>
              <section className="panel table-panel">
                <div className="table-wrap store-table">
                  <table>
                    <thead>
                      <tr>
                        <th onClick={() => sortBy("store")}>Магазин</th>
                        {(Object.keys(labels) as Metric[]).map((key) => (
                          <>
                            <th key={`${key}-v`} onClick={() => sortBy(key)}>
                              {labels[key]}
                            </th>
                            <th
                              key={`${key}-c`}
                              onClick={() => sortBy(`${key}_change`)}
                            >
                              Динамика
                            </th>
                          </>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr key={row.store} onClick={() => openStore(row)}>
                          <td>
                            <b>{row.store}</b>
                          </td>
                          {(Object.keys(labels) as Metric[]).map((key) => (
                            <>
                              <td key={`${key}-v`}>
                                {format(key, row.metrics[key].current)}
                              </td>
                              <td
                                key={`${key}-c`}
                              >
                                {comparisonsForStore(row.store,key).map(item=><div key={item.label} className={(item.metric?.change_percent || 0)>=0?"trend-up":"trend-down"}>{item.label}: {change(item.metric?.change_percent ?? null)}</div>)}
                              </td>
                            </>
                          ))}
                        </tr>
                      ))}
                      <tr className="network-total">
                        <td>ИТОГО ПО СЕТИ</td>
                        {(Object.keys(labels) as Metric[]).map((key) => (
                          <>
                            <td key={`${key}-v`}>
                              {format(key, data.metrics[key].current)}
                            </td>
                            <td key={`${key}-c`}>
                              {visibleComparisons.map(item=><div key={item.label}>{item.label}: {change(item.value!.metrics[key].change_percent)}</div>)}
                            </td>
                          </>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </>
      )}
      {selected && !loading && (
        <>
          <div className="backdrop" onClick={() => setSelected(undefined)} />
          <section className="store-detail">
            <div className="drawer-head">
              <div>
                <small>Детализация магазина</small>
                <h2>{selected.store}</h2>
              </div>
              <button onClick={() => setSelected(undefined)}>Закрыть</button>
            </div>
            <div className="drawer-body">
              <div className="kpis store-detail-kpis">
                {(Object.keys(labels) as Metric[]).map((key) => (
                  <div className="kpi" key={key}>
                    <span>{labels[key]}</span>
                    <strong>
                      {format(key, selected.metrics[key].current)}
                    </strong>
                    <small>
                      {comparisonsForStore(selected.store,key).map(item=><span key={item.label}>{item.label}: {change(item.metric?.change_percent ?? null)} </span>)}
                    </small>
                  </div>
                ))}
              </div>
              <section className="panel chart">
                <div className="section-title">
                  <h2>Динамика магазина</h2>
                  <div className="dynamics-controls">
                    <select
                      value={detailMetric}
                      onChange={(e) =>
                        setDetailMetric(e.target.value as typeof detailMetric)
                      }
                    >
                      <option value="revenue">Выручка</option>
                      <option value="checks">Чеки</option>
                      <option value="average_check">Средний чек</option>
                    </select>
                    <div className="chart-granularity">
                      {["day", "week", "month"].map((x) => (
                        <button
                          key={x}
                          className={
                            (group || dynamics?.group_by) === x ? "active" : ""
                          }
                          disabled={controller.busy}
                          onClick={() => selected && openStore(selected, x)}
                        >
                          {x === "day"
                            ? "По дням"
                            : x === "week"
                              ? "По неделям"
                              : "По месяцам"}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart
                    data={dynamics?.points || []}
                    margin={{ left: 32, right: 16 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="period" tickFormatter={date} />
                    <YAxis width={100} tickFormatter={number} />
                    <Tooltip
                      formatter={(v) =>
                        detailMetric === "revenue" ||
                        detailMetric === "average_check"
                          ? money(Number(v))
                          : number(Number(v))
                      }
                    />
                    <Line
                      dataKey={detailMetric}
                      stroke="#2878ff"
                      strokeWidth={3}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </section>
              <h3>Продажи магазина</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Дата</th>
                      <th>Документ</th>
                      <th>Клиент</th>
                      <th>Сумма</th>
                      <th>Товаров</th>
                      <th>Скидка</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sales?.items.map((x) => (
                      <tr
                        key={x.id}
                        className="clickable-row"
                        onClick={() => openSale(x.id)}
                      >
                        <td>{date(x.sale_date)}</td>
                        <td>{x.document_number}</td>
                        <td>{x.client || "—"}</td>
                        <td>{money(x.total_amount)}</td>
                        <td>{number(x.items)}</td>
                        <td>{percent(x.discount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        </>
      )}
      {sale && <SaleDrawer sale={sale} onClose={() => setSale(undefined)} />}
    </div>
  );
}
