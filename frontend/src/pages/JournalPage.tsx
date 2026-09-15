import {ChevronLeft,ChevronRight,Filter,RotateCcw,Search,Upload} from 'lucide-react';
import {useState} from 'react';
import {Link} from 'react-router-dom';
import {api,query} from '../api/client';
import {MultiSelect} from '../components/MultiSelect';
import {SaleDrawer} from '../components/SaleDrawer';
import {Empty,ErrorBox,Loading} from '../components/States';
import {useApi} from '../hooks/useApi';
import type {Sale,SalePage} from '../types';
import {date,money,percent} from '../utils/format';

type Filters={search:string;date_from:string;date_to:string;departments:string[];client:string;author:string;price_type:string;promotion:string;social:string;discount_card_percent:string;min_amount:string;max_amount:string;min_discount:string;max_discount:string};
type FilterOptions={departments:string[];authors:string[];price_types:string[];promotions:string[];discount_card_percents:number[]};
const initial:Filters={search:'',date_from:'',date_to:'',departments:[],client:'',author:'',price_type:'',promotion:'',social:'',discount_card_percent:'',min_amount:'',max_amount:'',min_discount:'',max_discount:''};

export function JournalPage(){
  const [draft,setDraft]=useState<Filters>(initial);const [filters,setFilters]=useState<Filters>(initial);
  const [page,setPage]=useState(1);const [pageSize,setPageSize]=useState(50);const [sort,setSort]=useState('sale_date');const [dir,setDir]=useState('desc');const [selected,setSelected]=useState<Sale>();
  const {data:options}=useApi<FilterOptions>('/filters');
  const cardOptions=Array.from(new Set([5,7,10,15,20,...(options?.discount_card_percents||[])])).sort((a,b)=>a-b);
  const path=`/sales?${query({...filters,page,page_size:pageSize,sort_by:sort,sort_dir:dir})}`;
  const {data,loading,error}=useApi<SalePage>(path);
  function apply(){setPage(1);setFilters(draft)}
  function reset(){setDraft(initial);setFilters(initial);setPage(1)}
  function sortBy(key:string){setSort(key);setDir(sort===key&&dir==='desc'?'asc':'desc')}
  async function open(id:number){setSelected(await api<Sale>(`/sales/${id}`))}
  return <div className="page">
    <div className="page-title"><div><h1>Журнал продаж</h1><p>Все загруженные продажи в едином реестре</p></div><Link className="primary" to="/imports"><Upload size={18}/>Загрузить XLS</Link></div>
    <section className="panel filters">
      <div className="search"><Search/><input placeholder="Документ, клиент, телефон, карта или товар" value={draft.search} onChange={e=>setDraft({...draft,search:e.target.value})} onKeyDown={e=>e.key==='Enter'&&apply()}/></div>
      <div className="filter-grid">
        <label>С даты<input type="date" value={draft.date_from} onChange={e=>setDraft({...draft,date_from:e.target.value})}/></label>
        <label>По дату<input type="date" value={draft.date_to} onChange={e=>setDraft({...draft,date_to:e.target.value})}/></label>
        <label>Подразделение<MultiSelect options={options?.departments||[]} value={draft.departments} onChange={departments=>setDraft({...draft,departments})} placeholder="Все подразделения"/></label>
        <label>Клиент<input value={draft.client} onChange={e=>setDraft({...draft,client:e.target.value})}/></label>
        <label>Автор<input value={draft.author} onChange={e=>setDraft({...draft,author:e.target.value})}/></label>
        <label>Тип цены<select value={draft.price_type} onChange={e=>setDraft({...draft,price_type:e.target.value})}><option value="">Все типы цен</option>{options?.price_types.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
        <label>Акция<input value={draft.promotion} onChange={e=>setDraft({...draft,promotion:e.target.value})}/></label>
        <label>Социальная<select value={draft.social} onChange={e=>setDraft({...draft,social:e.target.value})}><option value="">Все</option><option value="true">Да</option><option value="false">Нет</option></select></label>
        <label>Дисконтная карта<select value={draft.discount_card_percent} onChange={e=>setDraft({...draft,discount_card_percent:e.target.value})}><option value="">Любая скидка</option>{cardOptions.map(value=><option key={value} value={value}>{value}%</option>)}</select></label>
        <label>Сумма от<input type="number" value={draft.min_amount} onChange={e=>setDraft({...draft,min_amount:e.target.value})}/></label>
        <label>Сумма до<input type="number" value={draft.max_amount} onChange={e=>setDraft({...draft,max_amount:e.target.value})}/></label>
      </div>
      <div className="actions"><button className="primary" onClick={apply}><Filter size={17}/>Применить</button><button onClick={reset}><RotateCcw size={17}/>Сбросить</button></div>
    </section>
    <section className="panel table-panel"><div className="table-meta"><b>Найдено: {data?.total??0}</b><label>Строк<select value={pageSize} onChange={e=>{setPageSize(+e.target.value);setPage(1)}}>{[20,50,100,200].map(x=><option key={x}>{x}</option>)}</select></label></div>{loading?<Loading/>:error?<ErrorBox text={error}/>:!data?.items.length?<Empty/>:<div className="table-wrap journal"><table><thead><tr>{[['sale_date','Дата'],['document_number','№ документа'],['client','Клиент'],['department','Подразделение'],['total_amount','Сумма'],['base_amount','Базовая'],['discount_percent','Скидка'],['author','Автор'],['price_type','Тип цены'],['promotion','Акция']].map(([key,label])=><th key={key} onClick={()=>sortBy(key)}>{label}{sort===key?(dir==='desc'?' ↓':' ↑'):''}</th>)}</tr></thead><tbody>{data.items.map(s=><tr key={s.id} onClick={()=>open(s.id)}><td>{date(s.sale_date)}</td><td><b>{s.document_number}</b></td><td>{s.client||'—'}</td><td>{s.department}</td><td className="money">{money(s.total_amount)}</td><td>{money(s.base_amount)}</td><td>{percent(s.discount_percent)}</td><td>{s.author||'—'}</td><td>{s.price_type||'—'}</td><td>{s.promotion?<span className="badge">{s.promotion}</span>:'—'}</td></tr>)}</tbody></table></div>}<div className="pagination"><button disabled={page<=1} onClick={()=>setPage(page-1)}><ChevronLeft/></button><span>Страница <b>{page}</b> из {data?.pages??1}</span><button disabled={page>=(data?.pages??1)} onClick={()=>setPage(page+1)}><ChevronRight/></button></div></section>
    {selected&&<SaleDrawer sale={selected} onClose={()=>setSelected(undefined)}/>}
  </div>
}
