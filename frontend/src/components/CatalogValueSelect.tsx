import {ChevronDown} from 'lucide-react';
import {useEffect,useId,useRef,useState} from 'react';
import {query} from '../api/client';
import {useApi} from '../hooks/useApi';

/** A single catalogue value; editing/searching never submits an analytics report. */
export function CatalogValueSelect({field,label,value,onChange}:{field:'brand'|'manufacturer';label:string;value:string;onChange:(value:string)=>void}) {
  const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[term,setTerm]=useState('');
  const root=useRef<HTMLDivElement>(null),input=useRef<HTMLInputElement>(null),trigger=useRef<HTMLButtonElement>(null),id=useId();
  const {data,loading,error}=useApi<string[]>(open?`/reports/product-analytics/catalog-options/${field}?${query({search:term})}`:'');
  useEffect(()=>{const timer=setTimeout(()=>setTerm(search),250);return()=>clearTimeout(timer)},[search]);
  useEffect(()=>{
    if(open)input.current?.focus();
    else {setSearch('');setTerm('')}
  },[open]);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  function choose(next:string){onChange(next);setOpen(false);trigger.current?.focus()}
  const needle=search.trim().toLocaleLowerCase('ru');
  const options=[...new Set(['Не заполнено',...(data||[])])].filter(item=>item.toLocaleLowerCase('ru').includes(needle));
  return <div className="multi-select catalog-value-select" ref={root} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();setOpen(false);trigger.current?.focus()}}}>
    <button type="button" ref={trigger} className="multi-trigger" role="combobox" aria-label={label} aria-expanded={open} aria-haspopup="dialog" aria-controls={open?id:undefined} onClick={()=>setOpen(!open)}><span>{value||`Все ${field==='brand'?'бренды':'производители'}`}</span><ChevronDown size={16}/></button>
    {open&&<div className="multi-menu" id={id} role="dialog" aria-label={`Выбор: ${label}`}>
      <input ref={input} className="multi-search" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Поиск" aria-label={`Поиск: ${label}`}/>
      <button type="button" className="catalog-option" onClick={()=>choose('')}>Все {field==='brand'?'бренды':'производители'}</button>
      {loading&&<span className="multi-empty" role="status">Загрузка…</span>}
      {error?<span className="multi-empty" role="alert">Не удалось загрузить список. Закройте и откройте фильтр для повторной загрузки.</span>:<div className="catalog-option-list" role="listbox" aria-label={`${label}: значения`}>{options.map(option=><button type="button" role="option" aria-selected={value===option} className="catalog-option" key={option} onClick={()=>choose(option)}>{option}</button>)}{!options.length&&!loading&&<span className="multi-empty">Совпадений не найдено</span>}</div>}
    </div>}
  </div>;
}
