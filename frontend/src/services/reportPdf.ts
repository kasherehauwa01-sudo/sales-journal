import { reportResponse } from "../hooks/useExplicitReport";

export const FILTER_LABELS: Record<string,string> = {
 date_from:"Начало",date_to:"Конец",departments:"Подразделения",stores:"Магазины",store:"Магазин",
 manager:"Менеджер",managers:"Менеджеры",buyer_type:"Вид покупателя",buyer_types:"Виды покупателей",
 brands:"Бренды",brand:"Бренд",category:"Категория",manufacturers:"Производители",manufacturer:"Производитель",
 subcategories:"Подкатегории",segment:"Сегмент",search:"Поиск",article:"Артикул",products:"Товары",clients:"Клиенты",
 compare:"Сравнение",comparison_mode:"Режим сравнения",group:"Группировка",group_by:"Группировка",tab:"Раздел",
 years:"Годы",reason:"Причина",status:"Статус",criteria:"Критерии",use_seasonality:"Учитывать сезонность",
 seasonality_status:"Сезонность",min_revenue:"Минимальная выручка",min_purchases:"Минимум покупок",
 r_score:"R",f_score:"F",m_score:"M",rfm_code:"RFM-код",display:"Показ истории",
 compare_from:"Начало сравнения",compare_to:"Конец сравнения",period:"Период",period_kind:"Период",
};
function valueText(value: unknown): string {
 if(Array.isArray(value)) return value.map(valueText).join(", ");
 if(value && typeof value === 'object') {
  const v=value as Record<string,unknown>;
  return String(v.name ?? v.product_key ?? v.code ?? v.article ?? Object.entries(v).map(([k,val])=>`${FILTER_LABELS[k]||k}: ${valueText(val)}`).join('; '));
 }
 if(typeof value==='string') {const labels:Record<string,string>={month:'Месяц',week:'Неделя',day:'День',current_month:'Текущий месяц',previous_month:'Прошлый месяц',current_week:'Текущая неделя',previous_week:'Прошлая неделя',custom:'Произвольный период',previous_period:'Предыдущий период',previous_year:'Прошлый год',top:'Топ товаров',growth:'Рост',decline:'Падение',stopped:'Без продаж',new:'Новые',product:'Товар',candidates:'Кандидаты',history:'История'};return labels[value]||value;}
 return typeof value==='boolean' ? (value?'Да':'Нет') : String(value ?? '');
}
export function filterText(snapshot: Record<string,unknown>): string {
 const source = (snapshot.filters || snapshot.parameters || snapshot) as Record<string,unknown>;
 return Object.entries(source).filter(([key,value])=>
  !['page','after','limit','include_both','include_groupings'].includes(key) && value!==undefined && value!==null && value!=='' && (!Array.isArray(value)||value.length)
 ).map(([key,value])=>`${FILTER_LABELS[key]||key}: ${valueText(value)}`).join(' · ');
}
export function periodText(snapshot: Record<string,unknown>): string {
 const p=(snapshot.filters || snapshot.parameters || snapshot) as Record<string,unknown>;
 return [p.date_from,p.date_to].filter(Boolean).join(' — ') || 'Вся доступная история';
}
export function pdfFilename(report:string,snapshot:Record<string,unknown>) {
 const p=(snapshot.filters || snapshot.parameters || snapshot) as Record<string,unknown>;
 const dates=[p.date_from,p.date_to].filter(v=>typeof v==='string' && /^\d{4}-\d{2}-\d{2}$/.test(v as string));
 return `${report}_${dates.length?dates.join('_'):'all-history'}.pdf`;
}
export function assertSameSnapshot(before:unknown, after:unknown) {
 if(JSON.stringify(before)!==JSON.stringify(after)) throw new Error('Данные изменились после формирования отчёта. Сформируйте отчёт заново перед экспортом PDF.');
}
export function assertComplete(items:unknown[],total:number) {
 if(!Number.isSafeInteger(total)||total<0||items.length!==total)
  throw new Error('Сервер не вернул все строки отчёта. Обновите backend; неполный PDF не создан.');
}

// Split wide tables into column bands; every row and column is retained. Repeat
// identity columns so continuation pages can still be interpreted independently.
export function splitWideTables(root:HTMLElement) {
 for(const table of [...root.querySelectorAll('table')]) {
  const rows=[...table.rows];
  const count=rows.reduce((max,row)=>Math.max(max,row.cells.length),0);
  if(count<=9) continue;
  if(rows.some(row=>[...row.cells].some(cell=>cell.colSpan!==1||cell.rowSpan!==1)))
   throw new Error('Широкая таблица с объединёнными ячейками требует отдельной печатной компоновки.');
  const heads=[...(table.tHead?.rows[0]?.cells||[])].map(cell=>cell.textContent||'');
  const nameIndex=heads.findIndex(label=>/клиент|магазин|товар|наименование|категори/i.test(label));
  const codeIndex=heads.findIndex((label,i)=>i!==nameIndex && /артикул|код|документ/i.test(label));
  const identities=[nameIndex>=0?nameIndex:0,codeIndex>=0?codeIndex:(nameIndex===1?0:1)];
  const remaining=Array.from({length:count},(_,i)=>i).filter(i=>!identities.includes(i));
  const fragment=document.createDocumentFragment();
  for(let start=0;start<remaining.length;start+=7) {
   const indexes=[...identities,...remaining.slice(start,start+7)].sort((a,b)=>a-b);
   const label=document.createElement('div');label.className='pdf-table-band';
   label.textContent=`Блок столбцов ${Math.floor(start/7)+1} из ${Math.ceil(remaining.length/7)} · ключевые столбцы повторены`;
   fragment.append(label);
   const band=table.cloneNode(true) as HTMLTableElement;
   for(const row of [...band.rows]) for(let i=row.cells.length-1;i>=0;i--) if(!indexes.includes(i)) row.deleteCell(i);
   fragment.append(band);
  }
  table.replaceWith(fragment);
 }
}
const COPY_STYLE = ['display','color','background-color','border','border-radius','padding','margin','font-size','font-weight','font-style','line-height','text-align','vertical-align','grid-template-columns','gap','align-items','justify-content','flex-direction','flex-wrap','opacity','fill','stroke','stroke-width','white-space'];
async function waitFor<T>(promise:Promise<T>|undefined,signal:AbortSignal):Promise<T|undefined> {
 if(!promise) return;
 if(signal.aborted) throw new DOMException('Aborted','AbortError');
 return new Promise((resolve,reject)=>{
  const abort=()=>reject(new DOMException('Aborted','AbortError'));
  signal.addEventListener('abort',abort,{once:true});
  promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));
 });
}
export async function captureReport(root:HTMLElement,snapshot:Record<string,unknown>,signal:AbortSignal) {
 await waitFor(document.fonts?.ready,signal);
 await waitFor(Promise.all([...root.querySelectorAll('img')].map(async image=>{
  try {await image.decode();} catch {if(root.contains(image)) throw new Error('Не удалось загрузить изображение отчёта. PDF не создан.');}
 })),signal);
 // Fonts, layout and Recharts (animations explicitly disabled on report pages).
 await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())));
 if(signal.aborted) throw new DOMException('Aborted','AbortError');
 if(root.querySelector('[aria-busy="true"],.loading')) throw new Error('Дождитесь загрузки всех блоков отчёта.');
 const clone=root.cloneNode(true) as HTMLElement;
 const originals=[root,...root.querySelectorAll('*')];
 const copies=[clone,...clone.querySelectorAll('*')];
 for(let i=0;i<originals.length;i++) {
  if(i>0 && i%1000===0) {await new Promise(resolve=>setTimeout(resolve,0));if(signal.aborted) throw new DOMException("Aborted","AbortError");}
  const original=originals[i],copy=copies[i];
  if(!(original instanceof Element)||!(copy instanceof Element)) continue;
  const computed=getComputedStyle(original);
  const styles=COPY_STYLE.map(key=>`${key}:${computed.getPropertyValue(key)}`).filter(v=>!v.includes('url('));
  // Do not carry event handlers, URLs, secrets in data attributes or hidden inputs.
  for(const attr of [...copy.attributes]) if(!['class','style','data-pdf-exclude','data-pdf-detail','viewBox','width','height','d','points','x','y','x1','x2','y1','y2','dx','dy','fill','stroke','stroke-width','stroke-dasharray','transform','text-anchor','dominant-baseline','clip-path','id','offset','stop-color','stop-opacity','r','rx','ry','cx','cy','colspan','rowspan','preserveAspectRatio'].includes(attr.name)) copy.removeAttribute(attr.name);
  if(computed.display==='grid') {
   const count=computed.gridTemplateColumns.split(' ').length;
   styles.push(`grid-template-columns:repeat(${count},minmax(0,1fr))`);
  }
  if(original.matches('.recharts-wrapper,.recharts-responsive-container')) styles.push(`position:relative;width:100%;height:${computed.height}`);
  if(original.matches('.recharts-legend-wrapper')) styles.push(`position:absolute;left:${computed.left};top:${computed.top};bottom:${computed.bottom};width:100%`);
  copy.setAttribute('style',styles.join(';'));
  if(computed.display==='none'||computed.visibility==='hidden') copy.setAttribute('data-pdf-hidden','true');
  if(original instanceof HTMLSelectElement) copy.replaceWith(document.createTextNode(original.selectedOptions[0]?.textContent||''));
  else if(original instanceof HTMLInputElement) {
   const value=original.type==='checkbox'?(original.checked?'☑':'☐'):original.value;
   copy.replaceWith(document.createTextNode(value));
  } else if(original instanceof HTMLCanvasElement) {
   const img=document.createElement('img');img.src=original.toDataURL('image/png');img.width=original.width;img.height=original.height;copy.replaceWith(img);
  } else if(original instanceof HTMLImageElement && original.currentSrc) {
   if(!original.complete||!original.naturalWidth) throw new Error('Не все изображения загружены. Повторите экспорт после загрузки.');
   const canvas=document.createElement('canvas');canvas.width=original.naturalWidth;canvas.height=original.naturalHeight;
   const context=canvas.getContext('2d');if(!context) throw new Error('Не удалось сохранить изображение.');
   context.drawImage(original,0,0);
   try {copy.setAttribute('src',canvas.toDataURL('image/png'));} catch {throw new Error('Источник изображения запрещает включение в PDF (CORS). Требуется разрешение источника; изображение не было пропущено.');}
  }
 }
 clone.querySelectorAll('[data-pdf-hidden], [data-pdf-exclude],script,style,iframe,object,embed,.backdrop,.pagination,.analytics-build-actions,.analytics-request-error,.report-filters,.filter-grid,.filters,.dynamics-filters,.period-picker,.product-selector,.promotion-selected-download,.report-modal:not([data-pdf-detail])').forEach(node=>node.remove());
 // Dialogs/drawers that are displayed become ordinary report sections.
 clone.querySelectorAll('.drawer,.store-detail,.rfm-detail,.category-detail,.analytics-detail,.product-detail,.report-help-modal,.report-modal').forEach(node=>(node as HTMLElement).style.cssText+=';position:static!important;width:100%!important;height:auto!important;overflow:visible!important;');
 clone.querySelectorAll('details').forEach(node=>node.setAttribute('open',''));
 clone.querySelectorAll('button').forEach(button=>{
  if(button.closest('.rfm-segment-grid')) {const card=document.createElement('div');card.setAttribute('style',button.getAttribute('style')||'');card.append(...button.childNodes);button.replaceWith(card);}
  else if(button.closest('td,th,.tabs,.chart-granularity,.dynamics-controls')) button.replaceWith(document.createTextNode(button.textContent||''));
  else button.remove();
 });
 clone.querySelectorAll('.table-wrap').forEach(node=>(node as HTMLElement).style.cssText+=';overflow:visible!important;max-height:none!important;height:auto!important;');
 // Responsive SVGs retain their viewBox and vector paths; no bitmap chart screenshot.
 clone.querySelectorAll('.recharts-wrapper').forEach(node=>(node as HTMLElement).classList.add('pdf-chart'));
 const filters=document.createElement('p');filters.className='pdf-filters';filters.textContent=filterText(snapshot)||'Фильтры: без ограничений';
 clone.querySelector('.page-title')?.after(filters);
 splitWideTables(clone);
 clone.style.cssText='width:100%;padding:0;margin:0;';
 return clone.outerHTML;
}
export async function downloadReportPdf(report:string,html:string,snapshot:Record<string,unknown>,signal:AbortSignal) {
 const filename=pdfFilename(report,snapshot);
 const response=await reportResponse('/reports/pdf',signal,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report,html,period:periodText(snapshot),filename})});
 const blob=await response.blob();
 if(!response.headers.get('content-type')?.includes('application/pdf')||blob.size<100) throw new Error('Сервер вернул некорректный PDF.');
 if(signal.aborted) throw new DOMException('Aborted','AbortError');
 const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename;link.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function assertVisibleRows(before:unknown[],after:unknown[],key:string) {
 const found=new Map(after.map(row=>[(row as Record<string,unknown>)[key],row]));
 for(const row of before) assertSameSnapshot(row,found.get((row as Record<string,unknown>)[key]));
}
