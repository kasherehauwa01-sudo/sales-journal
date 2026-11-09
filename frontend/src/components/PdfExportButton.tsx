import { useEffect, useRef, useState } from 'react';
import { ReportHttpError } from '../hooks/useExplicitReport';
import { flushSync } from 'react-dom';
import { Download } from 'lucide-react';
import { assertSameSnapshot, PdfValidationError, captureReport, downloadReportPdf } from '../services/reportPdf';

export function usePdfData<T>(data:T) {
 const [override,setOverride]=useState<T>();
 return [override===undefined?data:override,(value:T|undefined)=>flushSync(()=>setOverride(value))] as const;
}
type Props={report:string;snapshot?:object;disabled?:boolean;prepare?:(signal:AbortSignal)=>Promise<void>;restore?:()=>void};
export function PdfExportButton({report,snapshot,disabled,prepare,restore}:Props) {
 const pending=useRef<AbortController|null>(null),button=useRef<HTMLButtonElement>(null);
 const latest=useRef({snapshot,disabled});latest.current={snapshot,disabled};
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>()=>pending.current?.abort(),[]);
 async function run() {
  if(pending.current||disabled||!snapshot) return;
  const controller=new AbortController();pending.current=controller;setBusy(true);setError('');
  const root=button.current?.closest('.page') as HTMLElement|null;
  if(!root){pending.current=null;setBusy(false);return;}
  const frozen=JSON.parse(JSON.stringify(snapshot)) as Record<string,unknown>;
  const originalInert=root.inert;root.inert=true;root.setAttribute('data-pdf-busy','true');
  const timeout=setTimeout(()=>controller.abort(),120000);
  let stage:'prepare'|'capture'|'render'='prepare';
  try {
   await prepare?.(controller.signal);
   const verifyParameters=()=>{
    if(latest.current.disabled) throw new PdfValidationError('parameters_changed','Изменились параметры фильтрации. Сформируйте отчёт заново.');
    assertSameSnapshot(frozen,JSON.parse(JSON.stringify(latest.current.snapshot??null)),'parameters');
   };
   verifyParameters();
   stage='capture';
   const html=await captureReport(root,frozen,controller.signal);
   verifyParameters();
   stage='render';
   await downloadReportPdf(report,html,frozen,controller.signal);
  } catch(e) { if(!controller.signal.aborted) setError(e instanceof PdfValidationError?`${e.message} [${e.code}:${e.check}]`:e instanceof Error?`${stage==='prepare'?'Ошибка подготовки данных PDF':stage==='capture'?'Ошибка печатной версии PDF':'Ошибка формирования PDF на сервере'}: ${e.message}${e instanceof ReportHttpError&&e.code?` [${e.code}]`:''}`:'Ошибка подготовки PDF. Повторите позже.'); else setError('Экспорт PDF прерван по времени или при закрытии отчёта. Файл не был сокращён.'); }
  finally { clearTimeout(timeout);try {restore?.();} finally {root.inert=originalInert;root.removeAttribute('data-pdf-busy');pending.current=null;setBusy(false);} }
 }
 return <div data-pdf-exclude className="pdf-export-action"><button ref={button} className="primary" disabled={disabled||!snapshot||busy} onClick={()=>void run()}><Download size={17}/>{busy?'Формируется PDF…':'Скачать PDF'}</button>{busy&&<span role="status">Подготовка полного отчёта…</span>}{error&&<p role="alert">{error}</p>}</div>;
}
