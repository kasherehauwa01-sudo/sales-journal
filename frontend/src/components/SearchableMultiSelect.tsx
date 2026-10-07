import {ChevronDown,Search} from 'lucide-react';
import {useEffect,useMemo,useRef,useState} from 'react';
import {api,query} from '../api/client';

type Option={value:string;label:string};type Response={items:Option[]};

export function SearchableMultiSelect({endpoint,value,onChange,placeholder='Все'}:{endpoint:string;value:string[];onChange:(value:string[])=>void;placeholder?:string}){
  const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[options,setOptions]=useState<Option[]>([]),[loading,setLoading]=useState(false);
  const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  useEffect(()=>{if(!open)return;let active=true;const timer=window.setTimeout(()=>{setLoading(true);api<Response>(`${endpoint}?${query({search,page:1,page_size:500})}`).then(data=>{if(active)setOptions(data.items)}).finally(()=>active&&setLoading(false))},300);return()=>{active=false;window.clearTimeout(timer)}},[endpoint,open,search]);
  const shown=useMemo(()=>{const byValue=new Map(options.map(option=>[option.value,option]));value.forEach(item=>{if(!byValue.has(item))byValue.set(item,{value:item,label:item})});return [...byValue.values()]},[options,value]);
  function toggle(option:string){onChange(value.includes(option)?value.filter(item=>item!==option):[...value,option])}
  return <div className="catalog-multiselect" ref={root}><button type="button" className="multi-trigger" onClick={()=>setOpen(!open)}><span>{value.length?`Выбрано: ${value.length}`:placeholder}</span><ChevronDown size={16}/></button>{open&&<div className="catalog-dropdown"><div className="catalog-search"><Search size={15}/><input aria-label="Поиск бренда" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Поиск бренда"/></div><div className="brand-list">{loading?<span className="multi-empty">Загрузка…</span>:shown.length?shown.map(option=><label key={option.value}><input type="checkbox" checked={value.includes(option.value)} onChange={()=>toggle(option.value)}/><span>{option.label}</span></label>):<span className="multi-empty">Бренды не найдены</span>}</div><div className="catalog-actions"><button type="button" onClick={()=>onChange(Array.from(new Set([...value,...options.map(option=>option.value)])))}>Выбрать все</button><button type="button" onClick={()=>onChange([])}>Сбросить</button></div></div>}</div>
}
