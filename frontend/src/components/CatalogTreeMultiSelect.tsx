import {ChevronDown,ChevronRight,X} from 'lucide-react';
import {useMemo,useState} from 'react';
import type {ReactNode} from 'react';
import {useApi} from '../hooks/useApi';

type RawNode={id?:string|number;key?:string;value?:string;name?:string;title?:string;label?:string;children?:RawNode[];items?:RawNode[]};
type Node={id:string;label:string;children:Node[]};
function source(payload:any):RawNode[]{if(Array.isArray(payload))return payload;for(const key of ['items','categories','sections','tree','data']){const value=payload?.[key];if(Array.isArray(value))return value;if(value&&typeof value==='object'){const nested=source(value);if(nested.length)return nested}}return []}
function normalize(raw:RawNode):Node{const label=String(raw.label??raw.name??raw.title??raw.value??raw.id??raw.key??'Без названия');return {id:String(raw.value??raw.id??raw.key??label),label,children:(raw.children??raw.items??[]).map(normalize)}}
function descendants(node:Node):string[]{return [node.id,...node.children.flatMap(descendants)]}
export function CatalogTreeMultiSelect({value,onChange,label='Раздел'}:{value:string[];onChange:(value:string[])=>void;label?:string}){
 const {data,loading,error}=useApi<any>('/reports/product-sales/catalog/tree');const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[expanded,setExpanded]=useState<Set<string>>(new Set());
 const nodes=useMemo(()=>source(data).map(normalize),[data]);const needle=search.trim().toLocaleLowerCase('ru-RU');
 function visible(node:Node):boolean{return !needle||node.label.toLocaleLowerCase('ru-RU').includes(needle)||node.children.some(visible)}
 function toggle(node:Node){const ids=descendants(node),all=ids.every(id=>value.includes(id));onChange(all?value.filter(id=>!ids.includes(id)):[...new Set([...value,...ids])])}
 function render(node:Node,depth=0):ReactNode{if(!visible(node))return null;const has=node.children.length>0,isOpen=expanded.has(node.id)||!!needle;return <div key={node.id} className="catalog-tree-node"><div style={{paddingLeft:depth*16}}>{has?<button type="button" className="tree-toggle" onClick={()=>setExpanded(old=>{const next=new Set(old);next.has(node.id)?next.delete(node.id):next.add(node.id);return next})}>{isOpen?<ChevronDown size={14}/>:<ChevronRight size={14}/>}</button>:<span className="tree-spacer"/>}<label><input type="checkbox" checked={descendants(node).every(id=>value.includes(id))} onChange={()=>toggle(node)}/><span>{node.label}</span></label></div>{has&&isOpen&&node.children.map(child=>render(child,depth+1))}</div>}
 return <div className="multi-select catalog-tree"><button type="button" className="multi-trigger" onClick={()=>setOpen(!open)}><span>{value.length?`${label}: выбрано ${value.length}`:`Все: ${label.toLocaleLowerCase('ru-RU')}`}</span><ChevronDown size={16}/></button>{open&&<div className="multi-menu"><input className="multi-search" placeholder="Поиск" value={search} onChange={e=>setSearch(e.target.value)}/><div className="multi-actions"><button type="button" onClick={()=>onChange([...new Set(nodes.flatMap(descendants))])}>Выбрать всё</button><button type="button" onClick={()=>onChange([])}><X size={13}/>Сбросить</button></div>{loading?<span>Загрузка…</span>:error?<span className="multi-empty">{error}</span>:nodes.length?nodes.map(node=>render(node)):<span className="multi-empty">Разделы не найдены</span>}</div>}</div>
}
