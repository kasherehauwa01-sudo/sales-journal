import {ChevronDown,ChevronRight,Search} from 'lucide-react';
import {useEffect,useMemo,useRef,useState} from 'react';

export type CatalogTreeNode={id:string;code:string;name:string;parent_id:string|null;children:CatalogTreeNode[]};

function filterTree(nodes:CatalogTreeNode[],search:string):CatalogTreeNode[]{
  const term=search.trim().toLocaleLowerCase('ru-RU');
  if(!term)return nodes;
  return nodes.flatMap(node=>{
    const children=filterTree(node.children||[],search);
    return node.name.toLocaleLowerCase('ru-RU').includes(term)||children.length?[{...node,children}]:[];
  });
}

function flatten(nodes:CatalogTreeNode[]):CatalogTreeNode[]{return nodes.flatMap(node=>[node,...flatten(node.children||[])])}

export function TreeMultiSelect({nodes,value,onChange,placeholder='Все разделы'}:{nodes:CatalogTreeNode[];value:string[];onChange:(value:string[])=>void;placeholder?:string}){
  const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[expanded,setExpanded]=useState<Set<string>>(new Set());
  const root=useRef<HTMLDivElement>(null),filtered=useMemo(()=>filterTree(nodes,search),[nodes,search]),all=useMemo(()=>flatten(nodes),[nodes]);
  useEffect(()=>{const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close)},[]);
  function toggleValue(code:string){onChange(value.includes(code)?value.filter(item=>item!==code):[...value,code])}
  function toggleExpanded(id:string){setExpanded(current=>{const next=new Set(current);next.has(id)?next.delete(id):next.add(id);return next})}
  function render(items:CatalogTreeNode[],depth=0):React.ReactNode{return items.map(node=>{
    const hasChildren=Boolean(node.children?.length),isExpanded=search.trim()?true:expanded.has(node.id);
    return <div key={node.id} className="tree-node"><div className="tree-row" style={{paddingLeft:`${depth*18}px`}}>{hasChildren?<button type="button" className="tree-toggle" aria-label={isExpanded?'Свернуть':'Раскрыть'} onClick={()=>toggleExpanded(node.id)}>{isExpanded?<ChevronDown size={15}/>:<ChevronRight size={15}/>}</button>:<span className="tree-spacer"/>}<label><input type="checkbox" checked={value.includes(node.code)} onChange={()=>toggleValue(node.code)}/><span>{node.name}</span></label></div>{hasChildren&&isExpanded&&render(node.children,depth+1)}</div>
  })}
  return <div className="catalog-multiselect" ref={root}>
    <button type="button" className="multi-trigger" onClick={()=>setOpen(!open)}><span>{value.length?`Выбрано: ${value.length}`:placeholder}</span><ChevronDown size={16}/></button>
    {open&&<div className="catalog-dropdown"><div className="catalog-search"><Search size={15}/><input aria-label="Поиск категории" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Поиск категории"/></div><div className="tree-list">{filtered.length?render(filtered):<span className="multi-empty">Категории не найдены</span>}</div><div className="catalog-actions"><button type="button" onClick={()=>onChange(all.map(node=>node.code))}>Выбрать все</button><button type="button" onClick={()=>onChange([])}>Сбросить</button></div></div>}
  </div>
}
