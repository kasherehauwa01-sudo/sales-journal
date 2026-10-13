import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {SalesDynamicsReportPage} from '../src/pages/SalesDynamicsReportPage';
vi.mock('recharts',()=>{const Box=({children}:any)=><div>{children}</div>;return {CartesianGrid:()=>null,Legend:()=>null,Line:({name}:any)=><span data-testid="comparison-line">{name}</span>,LineChart:({children,data}:any)=><div data-testid="chart-data" data-points={JSON.stringify(data)}>{children}</div>,ResponsiveContainer:Box,Tooltip:()=>null,XAxis:()=>null,YAxis:()=>null};});
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
  fireEvent.click(screen.getByRole('checkbox',{name:'Прошлый год'}));
  expect((screen.getByRole('checkbox',{name:'Прошлый год'}) as HTMLInputElement).checked).toBe(false);
  expect(reports()).toHaveLength(0);fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(reports()).toHaveLength(1));
  const params=new URL(reports()[0][0],'http://test').searchParams;
  expect(params.getAll('clients')).toEqual(['Клиент А','Клиент Б']);expect(params.get('include_both')).toBe('true');
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

it('preloads both comparisons and switches cards/lines locally with checkboxes below Generate',async()=>{
  const period={start:'2026-09-01',end:'2026-09-30'},comparison={current:10,previous:5,difference:5,change_percent:100};
  const metrics=Object.fromEntries(['revenue','sales_count','items_count','average_check','average_items_per_sale'].map(key=>[key,comparison]));
  const chart={group_by:'day',points:[{index:0,period:period.start,previous_period:'2026-08-01',current:{revenue:10},previous:{revenue:5}}]};
  const fetch=vi.fn(async(url:string)=>new Response(JSON.stringify(url.includes('/filters')?{departments:[]}:url.includes('/reports/sales-dynamics?')?{period,requested_period:period,previous_period:period,metrics,chart,comparisons:{previous_period:{period,metrics,chart},previous_year:{period,metrics,chart}}}:[])));
  vi.stubGlobal('fetch',fetch);render(<SalesDynamicsReportPage/>);
  const group=screen.getByRole('group',{name:'Отображать сравнения'});
  expect(group.previousElementSibling?.classList.contains('analytics-build-actions')).toBe(true);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await screen.findAllByTestId('comparison-line');
  const calls=()=>fetch.mock.calls.filter(([url])=>url.includes('/reports/sales-dynamics?'));
  expect(calls()).toHaveLength(1);expect(new URL(calls()[0][0],'http://test').searchParams.get('include_both')).toBe('true');
  expect(screen.getAllByTestId('comparison-line')).toHaveLength(3);
  fireEvent.click(screen.getByRole('checkbox',{name:'Предыдущий период'}));
  expect(screen.queryAllByText(/^Предыдущий период:/)).toHaveLength(0);
  expect(screen.getAllByTestId('comparison-line')).toHaveLength(2);
  fireEvent.click(screen.getByRole('checkbox',{name:'Прошлый год'}));
  expect(screen.queryAllByText(/^Прошлый год:/)).toHaveLength(0);
  expect(screen.getAllByTestId('comparison-line')).toHaveLength(1);
  fireEvent.click(screen.getByRole('checkbox',{name:'Предыдущий период'}));fireEvent.click(screen.getByRole('checkbox',{name:'Прошлый год'}));
  expect(screen.getAllByTestId('comparison-line')).toHaveLength(3);expect(calls()).toHaveLength(1);
  expect(screen.queryByText(/Параметры изменены/)).toBeNull();
});


it('switches day/week/month chart points without another request or dirty filters',async()=>{
  const period={start:'2026-09-01',end:'2026-09-30'},comparison={current:30,previous:10,difference:20,change_percent:200};
  const metrics=Object.fromEntries(['revenue','sales_count','items_count','average_check','average_items_per_sale'].map(key=>[key,comparison]));
  const chart=(group_by:string,values:number[])=>({group_by,points:values.map((revenue,index)=>({index,period:`2026-09-0${index+1}`,current:{revenue},previous:{revenue:revenue/2}}))});
  const chart_groupings={day:chart('day',[10,20]),week:chart('week',[30]),month:chart('month',[30])};
  const fetch=vi.fn(async(url:string)=>new Response(JSON.stringify(url.includes('/filters')?{departments:[]}:url.includes('/reports/sales-dynamics?')?{period,requested_period:period,previous_period:period,metrics,chart:chart_groupings.day,chart_groupings,comparisons:{previous_period:{period,metrics,chart:chart_groupings.day,chart_groupings},previous_year:{period,metrics,chart:chart_groupings.day,chart_groupings}}}:[])));
  vi.stubGlobal('fetch',fetch);render(<SalesDynamicsReportPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await screen.findByTestId('chart-data');
  const points=()=>JSON.parse(screen.getByTestId('chart-data').getAttribute('data-points')!);
  expect(points()).toHaveLength(2);
  for(const label of ['По неделям','По месяцам']){
    fireEvent.click(screen.getByRole('button',{name:label}));
    expect(points()).toHaveLength(1);expect(points()[0].currentValue).toBe(30);
    expect(screen.getByRole('button',{name:label}).className).toBe('active');
  }
  fireEvent.click(screen.getByRole('button',{name:'По дням'}));expect(points()).toHaveLength(2);
  expect(fetch.mock.calls.filter(([url])=>url.includes('/reports/sales-dynamics?'))).toHaveLength(1);
  expect(screen.queryByText(/Параметры изменены/)).toBeNull();
});
