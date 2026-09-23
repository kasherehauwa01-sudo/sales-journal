import {ChevronDown,X} from 'lucide-react';
import {useEffect,useRef,useState} from 'react';

export function MultiSelect({options,value,onChange,placeholder='Все'}:{options:string[];value:string[];onChange:(value:string[])=>void;placeholder?:string}){
  const [open,setOpen]=useState(false);
  const root=useRef<HTMLDivElement>(null);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  function toggle(option:string){onChange(value.includes(option)?value.filter(x=>x!==option):[...value,option])}
  return <div className="multi-select" ref={root}>
    <button type="button" className="multi-trigger" onClick={()=>setOpen(!open)}><span>{value.length?`Выбрано: ${value.length}`:placeholder}</span><ChevronDown size={16}/></button>
    {open&&<div className="multi-menu">{value.length>0&&<button type="button" className="multi-clear" onClick={()=>onChange([])}><X size={14}/>Очистить</button>}{options.length?options.map(option=><label key={option}><input type="checkbox" checked={value.includes(option)} onChange={()=>toggle(option)}/><span>{option}</span></label>):<span className="multi-empty">Подразделения не найдены</span>}</div>}
  </div>
}
