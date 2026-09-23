import {X} from 'lucide-react';
import {useEffect,useRef,useState} from 'react';
import {api,query} from '../api/client';

type Suggestion={value:string;label:string};
type Props={value:string;onChange:(value:string)=>void;field:'search'|'client';placeholder?:string;onEnter?:()=>void;options?:string[]};
export function Autocomplete({value,onChange,field,placeholder,onEnter,options}:Props){
  const [items,setItems]=useState<Suggestion[]>([]);const [open,setOpen]=useState(false);const [loading,setLoading]=useState(false);const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  useEffect(()=>{
    if(options){const term=value.trim().toLocaleLowerCase('ru');setItems(options.filter(item=>!term||item.toLocaleLowerCase('ru').includes(term)).slice(0,10).map(item=>({value:item,label:item})));return}
    if(!value.trim()){setItems([]);setOpen(false);return}
    const controller=new AbortController();const timer=setTimeout(()=>{setLoading(true);api<Suggestion[]>(`/suggestions?${query({q:value.trim(),field})}`,{signal:controller.signal}).then(result=>{setItems(result);setOpen(true)}).catch(error=>{if(error?.name!=='AbortError')setItems([])}).finally(()=>setLoading(false))},250);return()=>{clearTimeout(timer);controller.abort()}
  },[value,field,options]);
  function choose(item:Suggestion){onChange(item.value);setOpen(false)}
  function focus(){if(options){setOpen(true)}else if(value)setOpen(true)}
  return <div className="autocomplete" ref={root}><input value={value} placeholder={placeholder} autoComplete="off" onFocus={focus} onKeyDown={event=>event.key==='Enter'&&onEnter?.()} onChange={event=>onChange(event.target.value)}/>{value&&<button type="button" className="input-clear" aria-label="Очистить поле" onClick={()=>{onChange('');setOpen(Boolean(options))}}><X size={15}/></button>}{open&&<div className="autocomplete-menu">{loading?<span>Поиск…</span>:items.length?items.map((item,index)=><button type="button" key={`${item.value}-${index}`} onClick={()=>choose(item)}>{item.label}</button>):<span>Совпадений не найдено</span>}</div>}</div>
}
