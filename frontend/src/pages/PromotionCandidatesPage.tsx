import {ProductPhoto} from "../components/ProductPhoto";
import {CatalogValueSelect} from "../components/CatalogValueSelect";
import {ReportActions,ReportEmpty} from '../components/ReportActions';
import {useExplicitReport,reportJson,reportDownload} from '../hooks/useExplicitReport';
import {useEffect,useMemo,useRef,useState} from 'react';
import {Bar,BarChart,CartesianGrid,ResponsiveContainer,Tooltip,XAxis,YAxis} from 'recharts';
import {MultiSelect} from '../components/MultiSelect';import {Empty,Loading} from '../components/States';import {useApi} from '../hooks/useApi';import {date,money,number,percent} from '../utils/format';
type Row={catalog_manager?:string|null;image_url?:string|null;key:string;name:string;code?:string;article?:string;category:string;brand:string;manufacturer:string;reasons:string[];stop_factors:string[];status:string;revenue:number;units:number;checks:number;average_price:number;units_30:number;units_previous_30:number;units_60_90:number;units_change_percent?:number;category_change_percent?:number;category_deviation_pp?:number;stock?:number;aviatorov_stock?:number|null;stock_months?:number;stock_value?:number;days_without_sales?:number;last_sale?:string;monthly_units:number;seasonality?:{status:string;priority:number;index:number|null;confidence:number;start_month?:number;peak_month?:number;end_month?:number;action:string;reason:string;period:string}};
type Report={items:Row[];total:number;table_limit:number;summary:{candidates:number;stale:number;excess:number;decline:number};catalog_available:boolean;seasonality?:{as_of:string;years:number[];ready:boolean;last_success?:string;scope:string};limitations:string[]};
const columns=[["photo", "Фото", "Фотография товара из VRCatalog."], ["name", "Товар", "Наименование товара."], ["code", "Код / артикул", "Код товара; если его нет — артикул."], ["category", "Категория", "Текущая категория товара."], ["season", "Сезонность", "Повторяющийся спрос по двум полным годам продаж."], ["priority", "Сезонный приоритет", "Приоритет от 0 до 100; сезонность сама по себе не означает скидку."], ["reasons", "Причины", "Сработавшие критерии попадания в кандидаты."], ["units", "30 дней", "Проданные единицы за последние 30 дней выбранного периода."], ["previous", "Пред. 30", "Проданные единицы за предыдущие 30 дней."], ["change", "Изменение", "Изменение количества между двумя 30-дневными периодами, в процентах."], ["category_change", "Изменение категории", "Изменение продаж категории за те же периоды, в процентах."], ["stock", "Остаток", "Суммарный текущий остаток по всем складам."], ["aviatorov", "Остатки Авиаторов Зал+Склад", "Текущий остаток только склада Авиаторов Зал+Склад."], ["months", "Запас", "На сколько месяцев продаж хватит общего остатка при среднем темпе продаж."], ["days", "Без продажи", "Дней от последней продажи до конца выбранного периода."], ["status", "Статус", "Результат действующих критериев и стоп-факторов."], ["manager", "Менеджер", "Характеристика «Менеджер» товара из VRCatalog; не менеджер клиента и не Менеджер ОЗ."]] as const;
const columnStorage="promotion-candidates-columns-v2";
const defaultColumns=["photo","name","season","priority","reasons","aviatorov","status","manager"];
const iso=(value:Date)=>value.toISOString().slice(0,10);const end=new Date(),start=new Date(end.getFullYear(),end.getMonth()-6,1);
export function PromotionCandidatesPage() {
  const [visibleColumns,setVisibleColumns]=useState<string[]>(()=>{
    try {const current=localStorage.getItem(columnStorage);const saved=JSON.parse(current||localStorage.getItem("promotion-candidates-columns-v1")||"null");if(Array.isArray(saved)){const valid=saved.filter((x:unknown)=>typeof x==='string'&&columns.some(c=>c[0]===x));if(valid.length){const migrated=!current&&!valid.includes("manager")?[...valid,"manager"]:valid;if(!current)localStorage.setItem(columnStorage,JSON.stringify(migrated));return migrated;}}}catch{}
    return defaultColumns;
  });
  const [columnSettings,setColumnSettings]=useState(false);
  const [checkedKeys,setCheckedKeys]=useState<Set<string>>(()=>new Set());
  const allCheckbox=useRef<HTMLInputElement>(null);
  const toggleColumn=(key:string)=>setVisibleColumns(current=>{const next=current.includes(key)?current.filter(x=>x!==key):[...current,key];if(!next.length)return current;try{localStorage.setItem(columnStorage,JSON.stringify(next));}catch{}return next;});
  const [catalogDetail,setCatalogDetail]=useState<{stocks:{name:string;quantity:number|null}[];oz_manager:string|null}|null>(null);
  const [catalogError,setCatalogError]=useState("");

  const [tab, setTab] = useState<"candidates" | "history">("candidates"),
    [from, setFrom] = useState(iso(start)),
    [to, setTo] = useState(iso(end)),
    [selectedMonths, setSelectedMonths] = useState<number|null>(6),
    [departments, setDepartments] = useState<string[]>([]),
    [reason, setReason] = useState(""),
    [status, setStatus] = useState(""),
    [category, setCategory] = useState(""),
    [brand, setBrand] = useState(""),
    [manufacturer, setManufacturer] = useState(""),
    [manager, setManager] = useState(""),
    [buyerType, setBuyerType] = useState(""),
    [selected, setSelected] = useState<Row>(),
    [sort, setSort] = useState<keyof Row|"recommendation"|"seasonal_priority">("recommendation"),
    [useSeasonality,setUseSeasonality]=useState(true),
    [seasonalityStatus,setSeasonalityStatus]=useState("");
  useEffect(()=>{
    setCatalogDetail(null);setCatalogError("");
    if(!selected)return;
    const abort=new AbortController();
    reportJson<{stocks:{name:string;quantity:number|null}[];oz_manager:string|null}>("/reports/promotion-candidates/product-catalog",abort.signal,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:selected.code,article:selected.article})})
      .then(value=>{if(!abort.signal.aborted)setCatalogDetail(value);})
      .catch(()=>{if(!abort.signal.aborted)setCatalogError("Не удалось загрузить данные VRCatalog");});
    return ()=>abort.abort();
  },[selected]);
  const [criteria, setCriteria] = useState({
    decline_percent: -30,
    min_previous_units: 5,
    stale_days: 30,
    low_sales_units: 2,
    excess_stock_months: 6,
    min_stock: 1 as number | null,
    recent_promotion_months: 3,
  });
  const { data: filters } = useApi<{ departments: string[] }>("/filters"),
    { data: managers } = useApi<string[]>("/integrations/clients-vr/managers"),
    { data: buyerTypes } = useApi<string[]>(
      "/integrations/clients-vr/buyer-types",
    );
  const params = {
    tab,
    date_from: from,
    date_to: to,
    departments,
    reason: reason || undefined,
    status: status || undefined,
    category: category || undefined,
    brand: brand || undefined,
    manufacturer: manufacturer || undefined,
    manager: manager || undefined,
    buyer_type: buyerType || undefined,
    criteria,
    use_seasonality:useSeasonality,
    seasonality_status:seasonalityStatus||undefined,
  };
  type Result = {
    report?: Report;
    history?: { items: unknown[]; message: string };
  };
  const controller = useExplicitReport(
    params,
    async (p, signal): Promise<Result> => {
      setSelected(undefined);
      const { tab, ...payload } = p;
      const init = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
      return tab === "history"
        ? {
            history: await reportJson(
              "/reports/promotion-candidates/history",
              signal,
              init,
            ),
          }
        : {
            report: await reportJson(
              "/reports/promotion-candidates",
              signal,
              init,
            ),
          };
    },
  );
  const { data: result, loading } = controller;
  const data = result?.report,
    history = result?.history;
  const rows = useMemo(
    () =>
      sort==="recommendation" ? (data?.items || []) : [...(data?.items || [])].sort(
        (a, b) => sort==="seasonal_priority" ? (b.seasonality?.priority??0)-(a.seasonality?.priority??0) : Number(b[sort] ?? -Infinity) - Number(a[sort] ?? -Infinity),
      ),
    [data, sort],
  );
  useEffect(()=>setCheckedKeys(new Set()),[data,tab]);
  const checkedRows=rows.filter(row=>checkedKeys.has(row.key));
  const allChecked=rows.length>0&&checkedRows.length===rows.length;
  useEffect(()=>{if(allCheckbox.current)allCheckbox.current.indeterminate=checkedRows.length>0&&!allChecked;},[checkedRows.length,allChecked]);
  const checkRow=(key:string)=>setCheckedKeys(current=>{const next=new Set(current);if(next.has(key))next.delete(key);else next.add(key);return next;});
  async function exportSelected(){
    if(!checkedRows.length)return;
    await controller.perform("export",signal=>reportDownload("/reports/promotion-candidates/export-selected","promotion-candidates-selected.xlsx",signal,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({rows:checkedRows})}));
  }
  const setCriterion = (key: keyof typeof criteria, value: string) =>
    setCriteria({ ...criteria, [key]: key === "min_stock" && value.trim() === "" ? null : Number(value) });
  async function exportExcel() {
    if (!controller.snapshot) return;
    const { tab, ...payload } = controller.snapshot;
    await controller.perform("export", (signal) =>
      reportDownload(
        "/reports/promotion-candidates/export",
        "promotion-candidates.xlsx",
        signal,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      ),
    );
  }
  function preset(months: number) {
    const finish = new Date(to),
      begin = new Date(finish.getFullYear(), finish.getMonth() - months, 1);
    setFrom(iso(begin));
    setSelectedMonths(months);
  }
  return (
    <div className={`page ${checkedRows.length>0&&tab==="candidates"?"promotion-has-selection":""}`}>
      <div className="page-title">
        <div>
          <h1>Кандидаты в акцию</h1>
          <p>Объяснимые сигналы для маркетолога, а не автоматическое решение</p>
        </div>
        <button
          className="primary"
          disabled={
            controller.busy ||
            !controller.snapshot ||
            controller.snapshot.tab !== "candidates"
          }
          onClick={exportExcel}
        >
          Выгрузить в Excel
        </button>
      </div>
      <section className="panel report-builder">
        <div className="tabs">
          <button
            className={tab === "candidates" ? "active" : ""}
            onClick={() => setTab("candidates")}
          >
            Кандидаты
          </button>
          <button
            className={tab === "history" ? "active" : ""}
            onClick={() => setTab("history")}
          >
            История эффективности акций
          </button>
        </div>
        <div className="period-picker">
          {[3, 6, 12].map((x) => (
            <button key={x} type="button" className={selectedMonths===x?"active":""} aria-pressed={selectedMonths===x} onClick={() => preset(x)}>
              {x} месяцев
            </button>
          ))}
        </div>
        <div className="report-filters">
          <label>
            Дата с
            <input
              type="date"
              value={from}
              onChange={(e) => {setFrom(e.target.value);setSelectedMonths(null)}}
            />
          </label>
          <label>
            Дата по
            <input
              type="date"
              value={to}
              onChange={(e) => {setTo(e.target.value);setSelectedMonths(null)}}
            />
          </label>
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
              <option value="">Все виды</option>
              {buyerTypes?.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            Категория
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </label>
          <div className="report-filter">
            <span>Бренд</span>
            <CatalogValueSelect field="brand" label="Бренд" value={brand} onChange={setBrand}/>
          </div>
          <div className="report-filter">
            <span>Производитель</span>
            <CatalogValueSelect field="manufacturer" label="Производитель" value={manufacturer} onChange={setManufacturer}/>
          </div>
          <label>
            Сезонность
            <select value={seasonalityStatus} onChange={event=>setSeasonalityStatus(event.target.value)}>
              <option value="">Все товары</option>
              {["Сезон сейчас","Скоро сезон","Вне сезона","Сезонность не выявлена","Недостаточно данных"].map(value=><option key={value}>{value}</option>)}
            </select>
          </label>
          <label className="seasonality-toggle"><span>Учитывать сезонность</span><input type="checkbox" checked={useSeasonality} onChange={event=>setUseSeasonality(event.target.checked)}/></label>
          <label>
            Быстрый фильтр
            <select value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">Все кандидаты</option>
              {[
                "Продажи падают",
                "Давно не продавался",
                "Низкие продажи при наличии остатка",
                "Избыточный запас",
                "Раньше продавался лучше",
                "Падает сильнее категории",
              ].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            Состояние
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Все</option>
              <option>Кандидат</option>
              <option>Не рекомендуется сейчас</option>
            </select>
          </label>
        </div>
        <details>
          <summary>
            <b>Критерии отбора</b>
          </summary>
          <div className="report-filters">
            {(
              [
                ["decline_percent", "Падение, %", "Изменение количества за последние 30 дней выбранного периода относительно предыдущих 30 дней. Например, −30 означает падение на 30% и более."],
                ["min_previous_units", "Минимум продаж ранее", "Минимальное количество проданных единиц за предыдущие 30 дней для сигнала «Продажи падают». Помогает исключить случайные колебания редких продаж."],
                ["stale_days", "Дней без продажи", "Число дней от последней продажи до конца выбранного периода. При достижении порога и положительном остатке товар считается давно не продававшимся."],
                ["low_sales_units", "Низкие продажи, шт.", "Если за последние 30 дней продано не больше этого количества и есть остаток, появляется сигнал «Низкие продажи при наличии остатка»."],
                ["excess_stock_months", "Избыточный запас, мес.", "На сколько месяцев продаж хватит остатка при среднем темпе с первой продажи в выбранном периоде. Запас не ниже порога считается избыточным; при нулевом темпе показатель не рассчитывается."],
                ["min_stock", "Минимальный остаток Авиаторов Зал+Склад", "Порог применяется только к остатку склада «Авиаторов Зал+Склад». Пустое поле отключает этот критерий. При отсутствии данных остаток показан как «Нет данных»; общий остаток не подставляется."],
              ] as const
            ).map(([key, label, explanation]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  aria-label={label}
                  aria-describedby={`criterion-${key}`}
                  value={criteria[key] ?? ""}
                  onChange={(e) => setCriterion(key, e.target.value)}
                />
                <small id={`criterion-${key}`} className="criterion-help">{explanation}</small>
              </label>
            ))}
          </div>
        </details>
        <ReportActions report={controller} />
      </section>
      <ReportEmpty show={!result && !loading} />
      {!result ? (
        loading ? (
          <Loading />
        ) : null
      ) : controller.snapshot?.tab === "history" ? (
        <section className="panel">
          <h2>История эффективности акций</h2>
          <div className="report-warning">
            {history?.message || "Загрузка…"}
          </div>
          <p>
            Для достоверного расчёта Sales Journal должен сохранять «Вид товара»
            в каждой позиции продажи. Текущее свойство CatalogVR не является
            историей.
          </p>
        </section>
      ) : loading ? (
        <Loading />
      ) : !data ? null : (
        <>
          {data.limitations.map((x) => (
            <div className="report-warning" key={x}>
              {x}
            </div>
          ))}
          {data.seasonality&&<p className="seasonality-note">Сезонность на {date(data.seasonality.as_of)} · история {data.seasonality.years.join("–")} · {data.seasonality.scope}. {data.seasonality.last_success?`Последний завершённый расчёт: ${date(data.seasonality.last_success)}`:"Первичный расчёт ещё не завершён."} {!data.seasonality.ready&&"Готовые актуальные показатели отсутствуют: сезонный приоритет не применяется."}</p>}
          <div className="kpis">
            {[
              ["Кандидатов", data.summary.candidates],
              ["Залежавшихся", data.summary.stale],
              ["Избыточный запас", data.summary.excess],
              ["С падением продаж", data.summary.decline],
            ].map(([label, value]) => (
              <div className="kpi" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <section className="panel table-panel promotion-table-panel">
            <div className="table-meta">
              <b>
                Показано: {rows.length} из {data.total} (лимит{" "}
                {data.table_limit})
              </b>
              <label>
                Сортировка{" "}
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value as keyof Row|"recommendation"|"seasonal_priority")}
                >
                  <option value="recommendation">По рекомендации</option>
                  <option value="seasonal_priority">Сезонный приоритет</option>
                  <option value="stock_months">Запас, мес.</option>
                  <option value="units_change_percent">Падение, %</option>
                  <option value="days_without_sales">Дней без продажи</option>
                  <option value="stock">Остаток</option>
                  <option value="aviatorov_stock">Остатки Авиаторов Зал+Склад</option>
                </select>
              </label>
            </div>
            {!rows.length ? (
              <Empty text="Кандидаты не найдены" />
            ) : (
              <><div className="promotion-column-controls"><button type="button" aria-expanded={columnSettings} onClick={()=>setColumnSettings(!columnSettings)}>Настройка колонок</button>
              {columnSettings&&<div className="promotion-column-options">{columns.map(([key,label])=><label key={key}><input type="checkbox" checked={visibleColumns.includes(key)} onChange={()=>toggleColumn(key)}/>{label}</label>)}</div>}</div>
              <div className="table-wrap promotion-table-wrap">
                <table className="promotion-fit-table">
                  <thead>
                    <tr><th className="promotion-select-column"><input ref={allCheckbox} type="checkbox" aria-label={allChecked?"Снять все":"Выделить все"} title="Все строки текущей таблицы" checked={allChecked} onChange={()=>setCheckedKeys(allChecked?new Set():new Set(rows.map(row=>row.key)))}/></th>{columns.filter(c=>visibleColumns.includes(c[0])).map(([key,label,help])=><th key={key}>{label} <button type="button" className="column-info" title={help} aria-label={`Описание: ${label}`}>i</button></th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.map((x) => (
                      <tr key={x.key} onClick={() => setSelected(x)}>
                        <td className="promotion-select-column"><input type="checkbox" aria-label={`Выбрать товар: ${x.name}`} checked={checkedKeys.has(x.key)} onClick={event=>event.stopPropagation()} onChange={()=>checkRow(x.key)}/></td>
                        {visibleColumns.includes("photo") && (<td><ProductPhoto src={x.image_url} name={x.name} /></td>)}
                        {visibleColumns.includes("name") && (<td>
                          <b>{x.name}</b>
                        </td>)}
                        {visibleColumns.includes("code") && (<td>{x.code || x.article || "—"}</td>)}
                        {visibleColumns.includes("category") && (<td>{x.category}</td>)}
                        {visibleColumns.includes("season") && (<td><span className={`seasonality-badge ${x.seasonality?.status==="Сезон сейчас"?"season-now":x.seasonality?.status==="Скоро сезон"?"season-soon":x.seasonality?.status==="Вне сезона"?"season-off":x.seasonality?.status==="Сезонность не выявлена"?"season-none":"season-unknown"}`} title={x.seasonality?.reason}>{x.seasonality?.status||"Недостаточно данных"}</span></td>)}
                        {visibleColumns.includes("priority") && (<td>{x.seasonality?.priority??0}</td>)}
                        {visibleColumns.includes("reasons") && (<td>{x.reasons.join(", ") || "—"}</td>)}
                        {visibleColumns.includes("units") && (<td>{number(x.units_30)}</td>)}
                        {visibleColumns.includes("previous") && (<td>{number(x.units_previous_30)}</td>)}
                        {visibleColumns.includes("change") && (<td>
                          {x.units_change_percent == null
                            ? "—"
                            : percent(x.units_change_percent)}
                        </td>)}
                        {visibleColumns.includes("category_change") && (<td>
                          {x.category_change_percent == null
                            ? "—"
                            : percent(x.category_change_percent)}
                        </td>)}
                        {visibleColumns.includes("stock") && (<td>
                          {x.stock == null ? "Нет данных" : number(x.stock)}
                        </td>)}
                        {visibleColumns.includes("aviatorov") && (<td>{x.aviatorov_stock == null ? "Нет данных" : number(x.aviatorov_stock)}</td>)}
                        {visibleColumns.includes("months") && (<td>
                          {x.stock_months == null
                            ? "—"
                            : number(x.stock_months)}
                        </td>)}
                        {visibleColumns.includes("days") && (<td>{x.days_without_sales ?? "—"}</td>)}
                        {visibleColumns.includes("status") && (<td>{x.status}</td>)}
                        {visibleColumns.includes("manager") && (<td>{x.catalog_manager||"Нет данных"}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div></>
            )}
          </section>
        </>
      )}
      {checkedRows.length>0&&tab==="candidates"&&<div className="promotion-selected-download"><button className="primary" disabled={controller.busy} onClick={exportSelected}>Скачать XLS ({checkedRows.length})</button></div>}
      {selected && (
        <>
          <div className="backdrop" onClick={() => setSelected(undefined)} />
          <section className="product-detail">
            <div className="drawer-head">
              <div>
                <small>Кандидат в акцию</small>
                <h2>{selected.name}</h2>
              </div>
              <button onClick={() => setSelected(undefined)}>Закрыть</button>
            </div>
            <div className="drawer-body">
              <ProductPhoto src={selected.image_url} name={selected.name} large />
              <h3>Остатки по складам</h3>
              {catalogError?<p role="alert">{catalogError}</p>:!catalogDetail?<p>Загрузка данных…</p>:catalogDetail.stocks?.length?<div className="catalog-values promotion-warehouse-balances">{catalogDetail.stocks.map((stock,index)=><div key={index}><span>{stock.name}</span><b>{stock.quantity==null?"Нет данных":number(stock.quantity)}</b></div>)}</div>:<p>Нет данных об остатках</p>}
              <p><b>Менеджер ОЗ:</b> {catalogDetail?.oz_manager||"Нет данных"}</p>
              <h3>Почему кандидат</h3>
              <p>{selected.reasons.join(" · ") || "Сигналы не сработали"}</p>
              {selected.stop_factors.length > 0 && (
                <div className="report-warning">
                  Не рекомендуется сейчас: {selected.stop_factors.join(" · ")}
                </div>
              )}
              <div className="field-grid">
                <div className="field">
                  <span>Код</span>
                  <b>{selected.code || "—"}</b>
                </div>
                <div className="field">
                  <span>Артикул</span>
                  <b>{selected.article || "—"}</b>
                </div>
                <div className="field">
                  <span>Остаток</span>
                  <b>{selected.stock ?? "Нет данных"}</b>
                </div>
                <div className="field">
                  <span>Запас</span>
                  <b>
                    {selected.stock_months == null
                      ? "—"
                      : `${number(selected.stock_months)} мес.`}
                  </b>
                </div>
                <div className="field">
                  <span>Последняя продажа</span>
                  <b>{selected.last_sale ? date(selected.last_sale) : "—"}</b>
                </div>
                <div className="field">
                  <span>Выручка</span>
                  <b>{money(selected.revenue)}</b>
                </div>
              </div>
              <h3>Сезонность и рекомендация</h3>
              <p>{selected.seasonality?.status||"Недостаточно данных"} · Приоритет: {selected.seasonality?.priority??0} / 100 · Индекс: {selected.seasonality?.index==null?"—":number(selected.seasonality.index)} · Уверенность: {selected.seasonality?.confidence??0} / 100</p>
              <p>Ожидаемый период: {selected.seasonality?.period||"—"}</p>
              <p><b>{selected.seasonality?.action||"Сохранить оценку по действующим критериям"}</b></p>
              <p>{selected.seasonality?.reason||"Готовые актуальные показатели сезонности отсутствуют."}</p>
              <p className="muted">Это сезонность наблюдаемых продаж, а не доказанный спрос. Уверенность — оценка воспроизводимости, не вероятность. Исторические остатки неизвестны.</p>
              <h3>Динамика количества</h3>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart
                  data={[
                    { period: "60–90 дней", units: selected.units_60_90 },
                    {
                      period: "Предыдущие 30",
                      units: selected.units_previous_30,
                    },
                    { period: "Последние 30", units: selected.units_30 },
                  ]}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="period" />
                  <YAxis />
                  <Tooltip />
                  <Bar dataKey="units" fill="#2878ff" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
