import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {PdfExportButton} from '../src/components/PdfExportButton';
import {assertComplete,assertSameSnapshot,assertVisibleRows,filterText,pdfFilename,splitWideTables,captureReport,downloadReportPdf} from '../src/services/reportPdf';
vi.mock('../src/services/reportPdf',async original=>({...await original<typeof import('../src/services/reportPdf')>(),captureReport:vi.fn(async()=>'<h1>Тест</h1>'),downloadReportPdf:vi.fn(async()=>{})}));
afterEach(()=>{cleanup();vi.restoreAllMocks()});
it('captures filters and period and rejects incomplete or changed data',()=>{
 const snapshot={filters:{date_from:'2026-10-01',date_to:'2026-10-30',buyer_type:['Розница'],manager:['Тест']}};
 expect(pdfFilename('product-sales',snapshot)).toBe('product-sales_2026-10-01_2026-10-30.pdf');
 expect(filterText(snapshot)).toContain('Вид покупателя: Розница');
 expect(()=>assertComplete([],500)).toThrow('все строки');
 expect(()=>assertSameSnapshot({revenue:1},{revenue:2})).toThrow('изменились');
 expect(()=>assertVisibleRows([{id:1}],[{id:2}],'id')).toThrow('изменились');
});
it('retains all 500 rows and 24 columns in readable bands',()=>{
 const root=document.createElement('div');root.innerHTML='<table><thead><tr>'+Array.from({length:24},(_,i)=>`<th>Заголовок ${i}</th>`).join('')+'</tr></thead><tbody>'+Array.from({length:500},(_,row)=>'<tr>'+Array.from({length:24},(_,col)=>`<td>r${row}c${col}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
 splitWideTables(root);expect(root.querySelectorAll('table')).toHaveLength(4);
 for(const table of root.querySelectorAll('table')) {expect(table.tBodies[0].rows).toHaveLength(500);expect(table.rows[0].cells.length).toBeLessThanOrEqual(9)}
 for(let i=0;i<24;i++) expect(root.textContent).toContain(`r499c${i}`);
});
it('blocks double click and restores the page after failure',async()=>{
 let finish!:()=>void;const prepare=vi.fn(()=>new Promise<void>(r=>finish=r)),restore=vi.fn();
 vi.mocked(captureReport).mockRejectedValueOnce(new Error('Ошибка PDF'));
 render(<div className="page"><PdfExportButton report="rfm-analysis" snapshot={{date_from:'2026-10-01'}} prepare={prepare} restore={restore}/></div>);
 const button=screen.getByRole('button',{name:'Скачать PDF'});
 fireEvent.click(button);fireEvent.click(button);expect(prepare).toHaveBeenCalledTimes(1);
 finish();await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('Ошибка PDF'));
 expect(restore).toHaveBeenCalledTimes(1);expect(button.closest<HTMLElement>('.page')!.inert).toBeFalsy();
});
it('disables export before report generation',()=>{
 render(<div className="page"><PdfExportButton report="rfm-analysis" disabled /></div>);
 expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true);
});

it('accepts identical response values regardless of nested object key order',()=>{
 const initial={key:'code:1',revenue:100,metrics:{current:4,previous:2},image_url:null};
 const full={image_url:null,metrics:{previous:2,current:4},revenue:100,key:'code:1'};
 expect(()=>assertSameSnapshot(initial,full)).not.toThrow();
 expect(()=>assertVisibleRows([initial],[full],'key')).not.toThrow();
});

it('matches legitimate duplicate product keys as a multiset without discarding rows',()=>{
 const before=[{key:'code:1',name:'old name',amount:10},{key:'code:1',name:'new name',amount:20}];
 expect(()=>assertVisibleRows(before,[...before].reverse(),'key')).not.toThrow();
 expect(()=>assertVisibleRows(before,[before[1]],'key')).toThrow();
 expect(()=>assertVisibleRows([before[0],before[0]],[before[0]],'key')).toThrow();
 expect(()=>assertVisibleRows(before,[before[0],{...before[1],amount:21}],'key')).toThrow();
});
it.each([
 [{n:1},{n:'1'}], [{n:null},{}], [{n:1},{n:1.0000000001}],
 [{a:[1,2]},{a:[2,1]}], [{id:'01'},{id:'1'}], [{n:null},{n:0}],
])('rejects genuine field, type, identity and ordered array changes', (before,after)=>{
 expect(()=>assertSameSnapshot(before,after)).toThrow();
});
it('reports filter changes separately without exposing values',()=>{
 expect(()=>assertSameSnapshot({clients:['private-A']},{clients:['private-B']},'parameters')).toThrow('параметры');
 try {assertSameSnapshot({clients:['private-A']},{clients:['private-B']},'parameters')} catch(e) {expect(String(e)).not.toContain('private-')}
});

it('rejects parameters changed during prepare and restores interaction',async()=>{
 let finish!:()=>void;
 const prepare=()=>new Promise<void>(r=>finish=r),restore=vi.fn();
 vi.mocked(downloadReportPdf).mockClear();
 const view=render(<div className="page"><PdfExportButton report="rfm-analysis" snapshot={{manager:['A']}} prepare={prepare} restore={restore}/></div>);
 fireEvent.click(screen.getByRole('button'));
 view.rerender(<div className="page"><PdfExportButton report="rfm-analysis" snapshot={{manager:['B']}} prepare={prepare} restore={restore}/></div>);
 finish();
 await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('parameters_changed'));
 expect(downloadReportPdf).not.toHaveBeenCalled();
 expect(restore).toHaveBeenCalledOnce();
 expect(screen.getByRole('button').closest<HTMLElement>('.page')!.inert).toBeFalsy();
});
it('allows a successful manual retry after preparation fails',async()=>{
 vi.mocked(captureReport).mockResolvedValue('<h1>Complete</h1>');
 vi.mocked(downloadReportPdf).mockClear();
 const prepare=vi.fn().mockRejectedValueOnce(new Error('Не удалось загрузить все строки')).mockResolvedValueOnce(undefined);
 render(<div className="page"><PdfExportButton report="rfm-analysis" snapshot={{page:1}} prepare={prepare}/></div>);
 fireEvent.click(screen.getByRole('button'));
 await screen.findByRole('alert');
 fireEvent.click(screen.getByRole('button'));
 await waitFor(()=>expect(downloadReportPdf).toHaveBeenCalledOnce());
 expect(screen.queryByRole('alert')).toBeNull();
});

it.each([
 ['prepare','Ошибка подготовки данных PDF'],['capture','Ошибка печатной версии PDF'],['render','Ошибка формирования PDF на сервере'],
])('identifies the %s failure stage and does not retry automatically',async(stage,label)=>{
 const {ReportHttpError}=await import('../src/hooks/useExplicitReport');
 const failure=new ReportHttpError('TEST SAFE ERROR',503,'pdf_browser_unavailable');
 const prepare=vi.fn().mockImplementation(async()=>{if(stage==='prepare')throw failure});
 vi.mocked(captureReport).mockImplementation(async()=>{if(stage==='capture')throw failure;return '<h1>Complete</h1>'});
 vi.mocked(downloadReportPdf).mockImplementation(async()=>{if(stage==='render')throw failure});
 const restore=vi.fn();
 render(<div className="page"><PdfExportButton report="rfm-analysis" snapshot={{page:1}} prepare={prepare} restore={restore}/></div>);
 fireEvent.click(screen.getByRole('button'));
 await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain(label));
 expect(prepare).toHaveBeenCalledTimes(1);
 expect(restore).toHaveBeenCalledTimes(1);
 expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(false);
});
