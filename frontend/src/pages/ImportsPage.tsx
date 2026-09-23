import {CheckCircle2,FileSpreadsheet,RefreshCw,UploadCloud} from 'lucide-react';
import {useEffect,useRef,useState} from 'react';
import {api} from '../api/client';
import {ImportErrorModal} from '../components/ImportErrorModal';
import {Empty,ErrorBox} from '../components/States';
import type {ImportBatch} from '../types';
import {date,datetime,number} from '../utils/format';

export function ImportsPage(){
  const [items,setItems]=useState<ImportBatch[]>([]);
  const [files,setFiles]=useState<File[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [details,setDetails]=useState<ImportBatch>();
  const input=useRef<HTMLInputElement>(null);
  const load=()=>api<ImportBatch[]>('/imports').then(setItems).catch(e=>setError(e.message));
  useEffect(()=>{load();const timer=setInterval(load,5000);return()=>clearInterval(timer)},[]);
  async function upload(){
    if(!files.length)return;
    setBusy(true);setError('');const body=new FormData();files.forEach(f=>body.append('files',f));
    try{await api('/imports',{method:'POST',body});setFiles([]);if(input.current)input.current.value='';load()}
    catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function showError(item:ImportBatch){
    try{setDetails(await api<ImportBatch>(`/imports/${item.id}`))}
    catch(e){setError((e as Error).message)}
  }
  return <div className="page">
    <div className="page-title"><div><h1>Импорт данных</h1><p>Загрузка продаж из файлов XLS, XLSX и HTML</p></div></div>
    {error&&<ErrorBox text={error}/>}<section className="panel upload"><div className="drop" onClick={()=>input.current?.click()}><UploadCloud size={38}/><b>{files.length?`Выбрано файлов: ${files.length}`:'Выберите файлы или перетащите сюда'}</b><span>XLS/XLSX/HTML, исходные файлы сохраняются на сервере</span><input ref={input} hidden multiple type="file" accept=".xls,.xlsx,.html,.htm" onChange={e=>setFiles(Array.from(e.target.files||[]))}/></div>{files.map(f=><div className="file" key={f.name}><FileSpreadsheet/><span><b>{f.name}</b><small>{number(f.size/1024)} КБ</small></span></div>)}<button className="primary" disabled={!files.length||busy} onClick={upload}>{busy?<RefreshCw className="spin"/>:<UploadCloud/>}{busy?'Загрузка…':'Начать импорт'}</button><p className="hint">Обработка идёт в фоне. Ошибка одной строки не останавливает импорт остальных данных.</p></section>
    <div className="section-title"><h2>История импортов</h2><button onClick={load}><RefreshCw size={16}/>Обновить</button></div>
    <section className="panel">{!items.length?<Empty text="Импортов пока нет"/>:<div className="table-wrap"><table><thead><tr><th>Файл</th><th>Загружен</th><th>Период</th><th>Строк</th><th>Добавлено</th><th>Дубли</th><th>Ошибки</th><th>Статус</th><th>Время</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><b>{item.filename}</b><small>{number(item.file_size/1024)} КБ</small></td><td>{datetime(item.uploaded_at)}</td><td>{item.period_start?`${date(item.period_start)} — ${date(item.period_end)}`:'—'}</td><td>{number(item.total_rows)}</td><td>{number(item.added_rows)}</td><td>{number(item.duplicate_rows)}</td><td>{item.error_rows?<button className="error-count" onClick={()=>showError(item)}>Ошибки: {number(item.error_rows)}</button>:0}</td><td>{item.status==='failed'?<button className="status failed clickable" onClick={()=>showError(item)}><RefreshCw/>Ошибка</button>:<span className={`status ${item.status}`}>{item.status==='completed'?<CheckCircle2/>:<RefreshCw/>}{({queued:'В очереди',processing:'Обработка',completed:'Готово'} as Record<string,string>)[item.status]||item.status}</span>}</td><td>{item.duration_ms?`${number(item.duration_ms/1000)} с`:'—'}</td></tr>)}</tbody></table></div>}</section>
    {details&&<ImportErrorModal item={details} onClose={()=>setDetails(undefined)}/>}
  </div>
}
