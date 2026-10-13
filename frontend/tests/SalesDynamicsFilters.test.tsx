import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {SalesDynamicsReportPage} from '../src/pages/SalesDynamicsReportPage';
vi.mock('recharts',()=>{const Box=({children}:any)=><div>{children}</div>;return {CartesianGrid:()=>null,Legend:()=>null,Line:()=>null,LineChart:Box,ResponsiveContainer:Box,Tooltip:()=>null,XAxis:()=>null,YAxis:()=>null};});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('selects multiple clients with search and comparison only when explicitly generating',async()=>{
  const period={start:'2026-09-01',end:'2026-09-30'},comparison={current:10,previous:5,difference:5,change_percent:100};
  const metrics=Object.fromEntries(['revenue','sales_count','items_count','average_check','average_items_per_sale'].map(key=>[key,comparison]));
  const fetch=vi.fn(async(url:string)=>new Response(JSON.stringify(url.includes('/client-directory')?{items:['Клиент А','Клиент Б'],next_cursor:null}:url.includes('/filters')?{departments:[],clients:['Клиент А','Клиент Б']}:url.includes('/reports/sales-dynamics?')?{period,requested_period:period,previous_period:period,metrics,chart:{group_by:'day',points:[]}}:[])));
  vi.stubGlobal('fetch',fetch);render(<SalesDynamicsReportPage/>);
  const reports=()=>fetch.mock.calls.filter(([url])=>url.includes('/reports/sales-dynamics?'));
  expect(reports()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button',{name:'Клиент'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'Клиент А'}));
  fireEvent.change(screen.getByPlaceholderText('Поиск'),{target:{value:'Клиент Б'}});
  expect(screen.queryByRole('checkbox',{name:'Клиент А'})).toBeNull();
  fireEvent.click(await screen.findByRole('checkbox',{name:'Клиент Б'}));fireEvent.mouseDown(document.body);
  fireEvent.click(screen.getByRole('button',{name:'Прошлый год'}));
  expect(screen.getByRole('button',{name:'Прошлый год'}).getAttribute('aria-pressed')).toBe('true');
  expect(reports()).toHaveLength(0);fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(reports()).toHaveLength(1));
  const params=new URL(reports()[0][0],'http://test').searchParams;
  expect(params.getAll('clients')).toEqual(['Клиент А','Клиент Б']);expect(params.get('comparison_mode')).toBe('previous_year');
});

it('searches all clients on the server and loads further pages without losing selections',async()=>{
  const fetch=vi.fn(async(url:string)=>{
    const params=new URL(url,'http://test').searchParams;
    const result=url.includes('/client-directory')?(params.get('search')==='1049'?{items:['Клиент 1049'],next_cursor:null}:params.has('after')?{items:['Клиент Б'],next_cursor:null}:{items:['Клиент А'],next_cursor:'клиент а'}):url.includes('/filters')?{departments:[],clients:['Только первые 1000']}:[];
    return new Response(JSON.stringify(result));
  });
  vi.stubGlobal('fetch',fetch);render(<SalesDynamicsReportPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Клиент'}));
  fireEvent.click(await screen.findByRole('checkbox',{name:'Клиент А'}));
  fireEvent.click(screen.getByRole('button',{name:'Показать ещё'}));
  expect(await screen.findByRole('checkbox',{name:'Клиент Б'})).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox',{name:'Поиск клиентов'}),{target:{value:'1049'}});
  expect(await screen.findByRole('checkbox',{name:'Клиент 1049'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'Клиент'}).textContent).toContain('Выбрано: 1');
  expect(fetch.mock.calls.filter(([url])=>url.includes('/reports/sales-dynamics?'))).toHaveLength(0);
});

it('places the comparison toggle under filters and sends both modes only on Generate',async()=>{
  const period={start:'2026-09-01',end:'2026-09-30'},comparison={current:10,previous:5,difference:5,change_percent:100};
  const metrics=Object.fromEntries(['revenue','sales_count','items_count','average_check','average_items_per_sale'].map(key=>[key,comparison]));
  const fetch=vi.fn(async(url:string)=>new Response(JSON.stringify(url.includes('/filters')?{departments:[]}:url.includes('/reports/sales-dynamics?')?{period,requested_period:period,previous_period:period,metrics,chart:{group_by:'day',points:[]}}:[])));
  vi.stubGlobal('fetch',fetch);render(<SalesDynamicsReportPage/>);
  const toggle=screen.getByRole('group',{name:'Сравнение периодов'});
  expect(toggle.previousElementSibling?.classList.contains('report-filters')).toBe(true);
  const calls=()=>fetch.mock.calls.filter(([url])=>url.includes('/reports/sales-dynamics?'));
  fireEvent.click(screen.getByRole('button',{name:'Прошлый год'}));
  expect(calls()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(calls()).toHaveLength(1));
  expect(new URL(calls()[0][0],'http://test').searchParams.get('comparison_mode')).toBe('previous_year');
  await waitFor(()=>expect((screen.getByRole('button',{name:'Сформировать отчёт'}) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button',{name:'Предыдущий период'}));
  expect(calls()).toHaveLength(1);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(calls()).toHaveLength(2));
  expect(new URL(calls()[1][0],'http://test').searchParams.get('comparison_mode')).toBe('previous_period');
});
