import {useState} from 'react';
import {query} from '../api/client';
import {useApi} from '../hooks/useApi';
import {Empty,ErrorBox,Loading} from '../components/States';

type Metrics={known_phones?:string[];[key:string]:unknown};
type Row={analytical_client_key:string;display_name:string;eligible:boolean;reason:string;status:string;
 signal_type:string|null;score:string;confidence:string;features:Metrics;signal:Record<string,unknown>|null};
type SourceDecision={requested_as_of:string;effective_as_of:string|null;last_closed_business_day:string;
 declared_data_through:string|null;observed_data_through:string|null;accepted_data_through:string|null;
 business_freshness:string;coverage_status:string;coverage_method:string|null;reason_codes:string[];
 shadow_allowed:boolean;automation_allowed:false;source_scope_limitation:string;timezone?:string;
 evidence:{id:number;filename:string;imported_at:string|null;declared_start:string|null;declared_end:string|null}[]};
type Report={mode:string;automation_allowed:false;enabled:boolean;active_run:{status:string;phase:string;error_count:number}|null;
 source_decision?:SourceDecision|null;
 scope?:{department:string|null;buyer_types:string[]}|null;
 run:{id:string;as_of:string;stale:boolean;coverage_status:string;freshness:{status:string;reason:string};
 duration_seconds:number;generator_version:string;diagnostics:Record<string,number>}|null;
 items:Row[];total:number;next_after:string|null;page_size:number};

function SourceEvidence({decision}:{decision:SourceDecision}){
 const last=decision.evidence.at(-1);
 const imported=last?.imported_at?new Date(last.imported_at).toLocaleString('ru-RU',
  {timeZone:decision.timezone||'Europe/Moscow'}):'не завершён';
 return <section aria-label="Доступность данных"><h2>Доступность данных</h2><dl>
 {Object.entries({'Запрошенная дата':decision.requested_as_of,'Фактическая дата анализа':decision.effective_as_of,
 'Последний закрытый день':decision.last_closed_business_day,'Данные заявлены по':decision.declared_data_through,
 'Принятая граница данных':decision.accepted_data_through,'Наблюдаемая последняя продажа':decision.observed_data_through,
 'Последний импорт':last?`#${last.id} · ${last.filename} · ${imported} (${decision.timezone||'Europe/Moscow'})`:null,
 'Freshness':decision.business_freshness,'Coverage':decision.coverage_status,
 'Метод определения границы':decision.coverage_method,'Причина ограничения даты':decision.reason_codes.join(', ')
 }).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v||'Неизвестно'}</dd></div>)}
 </dl><p>DECLARED — период заявлен источником; полнота всех документов не подтверждена. Автоматизация запрещена.</p>
 {!decision.shadow_allowed&&<p role="alert">Новый расчёт заблокирован. Ранее опубликованные результаты не изменены.</p>}
 {decision.business_freshness==='STALE'&&decision.shadow_allowed&&<p>Источник устарел относительно текущего дня. Разрешён исторический расчёт на фактическую дату анализа.</p>}
 <p>Набор обязательных источников не установлен. Используется консервативное пересечение периодов зарегистрированных импортов.</p>
 <details><summary>Импорты, ограничивающие дату</summary><ul>{decision.evidence.map(e=><li key={e.id}>#{e.id} · {e.filename} · {e.declared_start||'?'} — {e.declared_end||'?'}</li>)}</ul></details>
 </section>;
}
const reasons=['INSUFFICIENT_HISTORY','IRREGULAR_PURCHASE_PATTERN','INTERVAL_TOO_LONG','NO_VALID_PURCHASE_DAYS','SOURCE_UNRELIABLE'];
const columns=[['last_purchase_date','Последняя покупка'],['days_without_purchase','Дней без покупки'],
 ['median_interval','Медиана, дней'],['mean_interval','Средний интервал'],['overdue_threshold','Порог просрочки'],
 ['churn_threshold','Порог оттока'],['deviation_ratio','Отклонение'],['gap_count','Интервалов'],
 ['distinct_purchase_days','Дней покупок'],['history_span_days','Размах истории'],['mad','MAD'],
 ['regularity_dispersion','Нерегулярность'],['average_check','Средний чек'],['reference_revenue_180','Выручка за 180 дней'],
 ['reference_revenue','Выручка за 28 дней (норма)']] as const;
const value=(v:unknown)=>v==null?'—':typeof v==='number'?Number(v.toFixed(4)).toString():String(v);

export function AnalyticsSignalsPage(){
 const [draft,setDraft]=useState({search:'',signal_type:'',status:'',reason:'',min_score:'0',min_confidence:'0',eligible:''});
 const [filters,setFilters]=useState(draft);const [cursors,setCursors]=useState<string[]>([]);
 const page=cursors.length+1;
 const qs=query({...Object.fromEntries(Object.entries(filters).filter(([,v])=>v!=='')),after:cursors.at(-1),page_size:50});
 const {data,loading,error}=useApi<Report>(`/reports/analytics-signals?${qs}`);
 function change(key:keyof typeof draft,v:string){setDraft({...draft,[key]:v})}
 return <div className="page"><div className="page-title"><div><h1>Сигналы · Shadow</h1>
 <p>Диагностика INACTIVITY. Задания, звонки и уведомления не создаются.</p></div></div>
 <section className="panel" style={{padding:16}}><strong>Только наблюдение · Автоматизация выключена</strong>
 <p>Клиент определяется по нормализованному имени. Телефоны — дополнительные признаки. Полнота источника не подтверждена.</p>
 {data?.scope&&<p>Подразделение: {data.scope.department||'Все'} · Вид покупателя: {data.scope.buyer_types.join(', ')||'Все'}</p>}
 {data?.active_run&&<p role="status">Текущий запуск: {data.active_run.status}, фаза {data.active_run.phase}, ошибок {data.active_run.error_count}.</p>}
 {data?.source_decision&&<SourceEvidence decision={data.source_decision}/>}
 {data?.run&&<><p>Срез: <b>{data.run.as_of}</b> · Coverage: <b>{data.run.coverage_status}</b> · Свежесть: <b>{data.run.freshness.status}</b> · Версия: {data.run.generator_version} · Длительность: {data.run.duration_seconds.toFixed(1)} с</p>
 {data.run.stale&&<p className="error">Срез устарел или источник изменился. Ожидается новый успешный shadow-запуск.</p>}
 <details><summary>Диагностика запуска</summary><dl>{Object.entries(data.run.diagnostics).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></details></>}
 </section>
 <form className="panel" style={{padding:16,display:'flex',flexWrap:'wrap',gap:12}} onSubmit={e=>{e.preventDefault();setFilters({...draft});setCursors([])}}>
 <label>Клиент<input aria-label="Клиент" value={draft.search} onChange={e=>change('search',e.target.value)}/></label>
 <label>Тип<select aria-label="Тип" value={draft.signal_type} onChange={e=>change('signal_type',e.target.value)}><option value="">Все</option><option>PURCHASE_OVERDUE</option><option>CHURN_RISK</option></select></label>
 <label>Статус<select aria-label="Статус" value={draft.status} onChange={e=>change('status',e.target.value)}><option value="">Все</option>{['ACTIVE','SUSPENDED','RESOLVED','NORMAL'].map(v=><option key={v}>{v}</option>)}</select></label>
 <label>Пригодность<select aria-label="Пригодность" value={draft.eligible} onChange={e=>change('eligible',e.target.value)}><option value="">Все</option><option value="true">ELIGIBLE</option><option value="false">NOT_ELIGIBLE</option></select></label>
 <label>Причина<select aria-label="Причина" value={draft.reason} onChange={e=>change('reason',e.target.value)}><option value="">Все</option>{reasons.map(v=><option key={v}>{v}</option>)}</select></label>
 <label>Мин. score<input aria-label="Мин. score" type="number" min="0" max="100" value={draft.min_score} onChange={e=>change('min_score',e.target.value)}/></label>
 <label>Мин. confidence<input aria-label="Мин. confidence" type="number" min="0" max="1" step="0.01" value={draft.min_confidence} onChange={e=>change('min_confidence',e.target.value)}/></label>
 <button type="submit">Применить</button></form>
 {loading?<Loading/>:error?<ErrorBox text={error}/>:!data?.run?<Empty text="Успешных shadow-запусков пока нет. Worker по умолчанию отключён."/>:
 <section className="panel"><p style={{padding:12}}>Клиентов по фильтру: {data.total}. ACTIVE сначала, затем score по убыванию.</p>
 {!data.items.length?<Empty/>:<div className="table-wrap"><table><thead><tr>{['Клиент','Аналитический ключ','Телефоны','Тип','Статус','Пригодность / причина','Score','Confidence',...columns.map(c=>c[1]),'Обнаружен','Срез / coverage / freshness','Версия'].map(k=><th key={k}>{k}</th>)}</tr></thead>
 <tbody>{data.items.map(r=><tr key={r.analytical_client_key}><td>{r.display_name}</td><td>{r.analytical_client_key}</td>
 <td>{r.features.known_phones?.join(', ')||'—'}{r.features.phones_may_be_truncated?' (до 10)':''}</td>
 <td>{r.signal_type||'—'}</td><td>{r.status}</td><td>{r.eligible?'ELIGIBLE':'NOT_ELIGIBLE'} / {r.reason}</td><td>{r.score}</td><td>{r.confidence}</td>
 {columns.map(([k])=><td key={k}>{value(r.features[k])}</td>)}<td>{value(r.signal?.detected_at)}</td>
 <td>{data.run!.as_of} / {data.run!.coverage_status} / {data.run!.freshness.status}</td><td>{data.run!.generator_version}</td></tr>)}</tbody></table></div>}
 <div style={{display:'flex',gap:16,padding:12}}><button disabled={page===1} onClick={()=>setCursors(c=>c.slice(0,-1))}>Назад</button>
 <span>Страница {page}</span><button disabled={!data.next_after} onClick={()=>setCursors(c=>[...c,data.next_after!])}>Далее</button></div></section>}
 </div>;
}
