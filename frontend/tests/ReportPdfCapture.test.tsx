import {expect,it} from 'vitest';
import {captureReport} from '../src/services/reportPdf';
it('retains informational segment cards and SVG while removing action controls and fixed screen heights',async()=>{
 const root=document.createElement('div');root.className='page';root.innerHTML='<div class="page-title"><h1>RFM</h1><div data-pdf-exclude>Подготовка полного отчёта…</div></div><div class="rfm-segment-grid"><button style="background-color:rgb(40,120,255)"><b>Новые</b><strong>500</strong><span>20% · 100 ₽</span></button></div><div style="height:9999px"><svg width="300" height="100" viewBox="0 0 300 100"><path d="M0 0L100 10" stroke="#2878ff"></path></svg><details><summary>Вывод</summary><p>Рекомендация</p></details></div>';
 document.body.append(root);
 try {
  const html=await captureReport(root,{manager:['Тестовый менеджер']},new AbortController().signal);
  expect(html).toContain('Новые');expect(html).toContain('500');expect(html).toContain('20% · 100 ₽');
  expect(html).not.toContain('Подготовка полного отчёта');expect(html).not.toContain('9999px');
  expect(html).toContain('viewBox="0 0 300 100"');expect(html).toContain('M0 0L100 10');
  expect(html).toContain('Рекомендация');expect(html).toContain('open=""');
  expect(html).toContain('Менеджер: Тестовый менеджер');
 } finally {root.remove()}
});
