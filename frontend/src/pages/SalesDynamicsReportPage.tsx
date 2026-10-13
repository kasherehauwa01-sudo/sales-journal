import {SalesClientSelect} from "../components/SalesClientSelect";
import {ReportActions,ReportEmpty} from '../components/ReportActions';
import {useExplicitReport,reportJson,reportDownload} from '../hooks/useExplicitReport';
import {useMemo,useState} from 'react';
import {CartesianGrid,Legend,Line,LineChart,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {query} from '../api/client';
import {Empty,Loading} from '../components/States';
import {useApi} from '../hooks/useApi';
import {date,money,number,percent} from '../utils/format';

type PeriodKind='yesterday'|'current_week'|'previous_week'|'current_month'|'previous_month'|'custom';
type Group='day'|'week'|'month';type Metric='revenue'|'sales_count'|'items_count'|'average_check'|'average_items_per_sale';
type Comparison={current:number;previous:number;difference:number;change_percent:number|null};
type Point={index:number;period?:string;previous_period?:string;current?:Record<string,number|string>;previous?:Record<string,number|string>};
type Charts=Partial<Record<Group,{group_by:Group;points:Point[]}>>;
type ComparisonResult={chart_groupings?:Charts;period:{start:string;end:string};metrics:Record<Metric,Comparison>;chart:{group_by:Group;points:Point[]}};
type Report={chart_groupings?:Charts;comparisons?:{previous_period:ComparisonResult;previous_year:ComparisonResult};period:{start:string;end:string};requested_period:{start:string;end:string};previous_period:{start:string;end:string};warning?:string|null;metrics:Record<Metric,Comparison>;chart:{group_by:Group;points:Point[]}};
const iso=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
function dates(kind:PeriodKind){const now=new Date(),from=new Date(now),to=new Date(now),day=(now.getDay()+6)%7;if(kind==='yesterday'){from.setDate(from.getDate()-1);to.setDate(to.getDate()-1)}if(kind==='current_week')from.setDate(from.getDate()-day);if(kind==='previous_week'){from.setDate(from.getDate()-day-7);to.setDate(from.getDate()+6)}if(kind==='current_month')from.setDate(1);if(kind==='previous_month'){from.setMonth(from.getMonth()-1,1);to.setDate(0)}return {date_from:iso(from),date_to:iso(to)}}
const periodOptions:Array<[PeriodKind,string]>=[['yesterday','Вчера'],['current_week','Текущая неделя'],['previous_week','Прошлая неделя'],['current_month','Текущий месяц'],['previous_month','Прошлый месяц'],['custom','Произвольный период']];
const metricOptions:Array<[Metric,string]>=[['revenue','Выручка'],['sales_count','Количество продаж'],['items_count','Количество товаров'],['average_check','Средний чек'],['average_items_per_sale','Товаров в чеке']];
const metricMoney=(key:Metric)=>key==='revenue'||key==='average_check';const formatMetric=(key:Metric,value:number)=>metricMoney(key)?money(value):number(value);
export function SalesDynamicsReportPage() {
  const initial = dates("current_month");
  const [periodKind, setPeriodKind] = useState<PeriodKind>("current_month");
  const [dateFrom, setDateFrom] = useState(initial.date_from);
  const [dateTo, setDateTo] = useState(initial.date_to);
  const [department, setDepartment] = useState("");
  const [manager, setManager] = useState("");
  const [buyerType, setBuyerType] = useState("");
  const [showPrevious,setShowPrevious]=useState(true),[showYear,setShowYear]=useState(true);
  const [selectedClients, setSelectedClients] = useState<string[]>([]);
  const [group, setGroup] = useState<Group | "">("");
  const [metric, setMetric] = useState<Metric>("revenue");
  const { data: filters } = useApi<{ departments: string[]; clients?: string[] }>("/filters");
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
    include_groupings: true,
    department,
    manager,
    buyer_type: buyerType,
    clients: selectedClients,
    include_both: true,
  };
  const controller = useExplicitReport(params, (p, signal) =>
    reportJson<Report>(`/reports/sales-dynamics?${query(p)}`, signal),
  );
  const { data, loading } = controller;
  function selectPeriod(value: PeriodKind) {
    setPeriodKind(value);
    setGroup("");
    if (value !== "custom") {
      const next = dates(value);
      setDateFrom(next.date_from);
      setDateTo(next.date_to);
    }
  }
  const previousComparison=data?.comparisons?.previous_period;
  const yearComparison=data?.comparisons?.previous_year;
  const chartData=useMemo(()=>{
    const grouping=group || data?.chart.group_by || "day";
    const previous=data?.comparisons?.previous_period.chart_groupings?.[grouping]?.points||data?.chart_groupings?.[grouping]?.points||data?.comparisons?.previous_period.chart.points||data?.chart.points||[],year=data?.comparisons?.previous_year.chart_groupings?.[grouping]?.points||data?.comparisons?.previous_year.chart.points||[];
    const currentCount=previous.filter(point=>point.current).length;
    const count=Math.max(currentCount,showPrevious?previous.length:0,showYear?year.length:0);
    return Array.from({length:count},(_,index)=>{
      const current=previous[index]?.current||year[index]?.current;
      return {index,period:previous[index]?.period||year[index]?.period,previous_period:previous[index]?.previous_period,year_period:year[index]?.previous_period,currentValue:current?Number(current[metric]):null,previousValue:previous[index]?.previous?Number(previous[index].previous![metric]):null,yearValue:year[index]?.previous?Number(year[index].previous![metric]):null};
    });
  },[data,group,metric,showPrevious,showYear]);
  const cards: Metric[] = [
    "revenue",
    "sales_count",
    "items_count",
    "average_check",
    "average_items_per_sale",
  ];
  return (
    <div className="page sales-dynamics-report">
      <div className="page-title">
        <div>
          <h1>Динамика продаж</h1>
          <p>Отчеты · сравнение с предыдущим периодом и прошлым годом</p>
        </div>
      </div>
      <section className="panel dynamics-filters">
        <div className="period-picker">
          {periodOptions.map(([key, label]) => (
            <button
              className={periodKind === key ? "active" : ""}
              onClick={() => selectPeriod(key)}
              key={key}
            >
              {label}
            </button>
          ))}
        </div>
        {periodKind === "custom" && (
          <div className="custom-dates">
            <label>
              Дата начала
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </label>
            <label>
              Дата окончания
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
            Подразделение
            <select
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
            >
              <option value="">Все подразделения</option>
              {filters?.departments.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
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
              {buyerTypes?.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            Клиент
            <SalesClientSelect value={selectedClients} onChange={setSelectedClients} />
          </label>
        </div>
        <b className="selected-period">
          Выбранный период: {date(dateFrom)} — {date(dateTo)}
        </b>
        <ReportActions report={controller} />
        <div className="dynamics-comparison-checkboxes" role="group" aria-label="Отображать сравнения">
          <label><input type="checkbox" checked={showPrevious} onChange={event=>setShowPrevious(event.target.checked)}/>Предыдущий период</label>
          <label><input type="checkbox" checked={showYear} onChange={event=>setShowYear(event.target.checked)}/>Прошлый год</label>
        </div>
      </section>
      <ReportEmpty show={!data && !loading} />
      {!loading && data?.warning && (
        <div className="report-warning">{data.warning}</div>
      )}
      {loading ? (
        <Loading />
      ) : !data ? null : (
        <>
          {data.metrics.sales_count.current === 0 && (
            <Empty text="За выбранный период продажи не найдены" />
          )}
          <div className="kpis dynamics-kpis">
            {cards.map((key) => {
              const item = data.metrics[key],
                label = metricOptions.find((x) => x[0] === key)?.[1];
              return (
                <div className="kpi" key={key}>
                  <span>{label}</span>
                  <strong>{formatMetric(key, item.current)}</strong>
                  {[[showPrevious,"Предыдущий период",previousComparison?.metrics[key]||item],[showYear,"Прошлый год",yearComparison?.metrics[key]]].map(([show,title,comparison])=>{
                    const compared=comparison as Comparison|undefined;if(!show||!compared)return null;
                    return <div className="dynamics-card-comparison" key={String(title)}><small>{String(title)}: {formatMetric(key,compared.previous)}</small><small className={compared.difference>=0?"up":"down"}>{compared.difference>=0?"+":""}{formatMetric(key,compared.difference)} · {compared.change_percent==null?"Нет данных для сравнения":percent(compared.change_percent)}</small></div>;
                  })}
                </div>
              );
            })}
          </div>
          <section className="panel chart">
            <div className="section-title">
              <div>
                <h2>Динамика продаж</h2>
                <p>
                  {date(data.period.start)} — {date(data.period.end)} ·
                  {showPrevious&&<>Предыдущий период: {date(previousComparison?.period.start||data.previous_period.start)} — {date(previousComparison?.period.end||data.previous_period.end)} </>}
                  {showYear&&yearComparison&&<>Прошлый год: {date(yearComparison.period.start)} — {date(yearComparison.period.end)}</>}

                </p>
              </div>
              <div className="dynamics-controls">
                <select
                  value={metric}
                  onChange={(e) => setMetric(e.target.value as Metric)}
                >
                  {metricOptions.map(([key, label]) => (
                    <option value={key} key={key}>
                      {label}
                    </option>
                  ))}
                </select>
                <div className="chart-granularity">
                  {(
                    [
                      ["day", "По дням"],
                      ["week", "По неделям"],
                      ["month", "По месяцам"],
                    ] as const
                  ).map(([key, label]) => (
                    <button
                      className={
                        (group || data.chart.group_by) === key ? "active" : ""
                      }
                      onClick={() => setGroup(key)}
                      key={key}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {chartData.length ? (
              <ResponsiveContainer width="100%" height={340}>
                <LineChart
                  data={chartData}
                  margin={{ left: 32, right: 16, top: 8, bottom: 8 }}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="index"
                    tickFormatter={(_, index) => date(chartData[index]?.period)}
                  />
                  <YAxis width={110} tickFormatter={number} />
                  <Tooltip
                    labelFormatter={(_,items)=>date(items?.[0]?.payload?.period)}
                    formatter={(value,name,item)=>[`${formatMetric(metric,Number(value))} · ${date(name==="Прошлый год"?item.payload.year_period:name==="Предыдущий период"?item.payload.previous_period:item.payload.period)}`,String(name)]}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="currentValue"
                    name="Текущий период"
                    stroke="#2878ff"
                    strokeWidth={3}
                  />
                  {showPrevious&&(<Line
                    type="monotone"
                    dataKey="previousValue"
                    name="Предыдущий период"
                    stroke="#9b6cff"
                    strokeDasharray="6 4"
                    strokeWidth={2}
                  />)}
                  {showYear&&<Line type="monotone" dataKey="yearValue" name="Прошлый год" stroke="#10a087" strokeDasharray="3 3" strokeWidth={2}/>}
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <Empty text="За выбранный период продажи не найдены" />
            )}
          </section>
          <section className="panel">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Период</th>
                    <th>Выручка</th>
                    <th>Продажи</th>
                    <th>Товары</th>
                    <th>Средний чек</th>
                    <th>Товаров в чеке</th>
                  </tr>
                </thead>
                <tbody>
                  {data.chart.points
                    .filter((x) => x.current)
                    .map((row) => (
                      <tr key={row.index}>
                        <td>{date(row.period)}</td>
                        <td>{money(Number(row.current?.revenue || 0))}</td>
                        <td>{number(Number(row.current?.sales_count || 0))}</td>
                        <td>{number(Number(row.current?.items_count || 0))}</td>
                        <td>
                          {money(Number(row.current?.average_check || 0))}
                        </td>
                        <td>
                          {number(
                            Number(row.current?.average_items_per_sale || 0),
                          )}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
