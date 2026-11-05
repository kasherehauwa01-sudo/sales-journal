import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {PdfExportButton} from '../src/components/PdfExportButton';
import {assertComplete,assertSameSnapshot,assertVisibleRows,filterText,pdfFilename,splitWideTables,captureReport} from '../src/services/reportPdf';
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
