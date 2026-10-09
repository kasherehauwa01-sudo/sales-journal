import {ChevronDown,X} from 'lucide-react';
import {useEffect,useRef,useState} from 'react';

export function MultiSelect({options,value,onChange,placeholder='Все',searchable=false,showActions=false,emptyText='Значения не найдены',search='',onSearch}:{options:string[];value:string[];onChange:(value:string[])=>void;placeholder?:string;searchable?:boolean;showActions?:boolean;emptyText?:string;search?:string;onSearch?:(value:string)=>void}){
  const [open,setOpen]=useState(false);
  const [localSearch,setLocalSearch]=useState('');
  const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  function toggle(option:string){onChange(value.includes(option)?value.filter(x=>x!==option):[...value,option])}
  const needle=(onSearch?search:localSearch).trim().toLocaleLowerCase('ru-RU');
  const visible=needle?options.filter(option=>option.toLocaleLowerCase('ru-RU').includes(needle)):options;
  const setSearch=(next:string)=>{if(onSearch)onSearch(next);else setLocalSearch(next)};
  return <div className="multi-select" ref={root}>
    <button type="button" className="multi-trigger" onClick={()=>setOpen(!open)}><span>{value.length?`Выбрано: ${value.length}`:placeholder}</span><ChevronDown size={16}/></button>
    {open&&<div className="multi-menu">{searchable&&<input className="multi-search" value={onSearch?search:localSearch} onChange={event=>setSearch(event.target.value)} placeholder="Поиск"/>}{showActions&&<div className="multi-actions"><button type="button" onClick={()=>onChange([...new Set([...value,...visible])])}>Выбрать все</button><button type="button" onClick={()=>onChange([])}><X size={13}/>Сбросить</button></div>}{!showActions&&value.length>0&&<button type="button" className="multi-clear" onClick={()=>onChange([])}><X size={14}/>Очистить</button>}{visible.length?visible.map(option=><label key={option}><input type="checkbox" checked={value.includes(option)} onChange={()=>toggle(option)}/><span>{option}</span></label>):<span className="multi-empty">{emptyText}</span>}</div>}
  </div>
}
