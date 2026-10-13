import {useEffect,useRef,useState} from 'react';
import {api,query} from '../api/client';
type Page={items:string[];next_cursor:string|null};
export function SalesClientSelect({value,onChange}:{value:string[];onChange:(value:string[])=>void}){
  const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[items,setItems]=useState<string[]>([]),[cursor,setCursor]=useState<string|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const root=useRef<HTMLDivElement>(null),request=useRef<AbortController|null>(null),epoch=useRef(0),busy=useRef(false);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  useEffect(()=>{
    const version=++epoch.current;request.current?.abort();busy.current=false;
    if(!open)return;
    setItems([]);setCursor(null);setError('');setLoading(true);
    const abort=new AbortController();request.current=abort;
    const timer=setTimeout(()=>{busy.current=true;api<Page>(`/reports/sales-dynamics/client-directory?${query({search})}`,{signal:abort.signal}).then(page=>{if(version!==epoch.current||abort.signal.aborted)return;setItems(page.items);setCursor(page.next_cursor);}).catch(()=>{if(version===epoch.current&&!abort.signal.aborted)setError('Не удалось загрузить клиентов. Закройте и откройте фильтр для повтора.');}).finally(()=>{if(version===epoch.current){busy.current=false;setLoading(false);}});},250);
    return()=>{clearTimeout(timer);abort.abort();request.current?.abort();};
  },[open,search]);
  async function more(){
    if(busy.current||loading||!cursor)return;busy.current=true;setLoading(true);setError('');
    const version=epoch.current,abort=new AbortController();request.current=abort;
    try{const page=await api<Page>(`/reports/sales-dynamics/client-directory?${query({search,after:cursor})}`,{signal:abort.signal});if(version===epoch.current&&!abort.signal.aborted){setItems(current=>[...new Set([...current,...page.items])]);setCursor(page.next_cursor===cursor?null:page.next_cursor);}}
    catch{if(version===epoch.current&&!abort.signal.aborted)setError('Не удалось загрузить следующую страницу клиентов.');}
    finally{if(version===epoch.current){busy.current=false;setLoading(false);}}
  }
  return <div className="multi-select" ref={root}><button type="button" className="multi-trigger" aria-label="Клиент" aria-expanded={open} onClick={()=>setOpen(!open)}>{value.length?`Выбрано: ${value.length}`:'Все клиенты'}</button>{open&&<div className="multi-menu"><input className="multi-search" aria-label="Поиск клиентов" placeholder="Поиск" maxLength={200} value={search} onChange={event=>setSearch(event.target.value)}/>{value.length>0&&<button type="button" className="multi-clear" onClick={()=>onChange([])}>Очистить</button>}{items.map(name=><label key={name}><input type="checkbox" checked={value.includes(name)} onChange={()=>onChange(value.includes(name)?value.filter(item=>item!==name):[...value,name])}/>{name}</label>)}{loading&&<span role="status">Загрузка клиентов…</span>}{error&&<p role="alert">{error}</p>}{!loading&&!error&&!items.length&&<span className="multi-empty">Клиенты не найдены</span>}{cursor&&<button type="button" disabled={loading} onClick={more}>Показать ещё</button>}</div>}</div>;
}
