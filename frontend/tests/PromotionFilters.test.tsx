import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {PromotionCandidatesPage} from '../src/pages/PromotionCandidatesPage';

let fetchMock:ReturnType<typeof vi.fn>;
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status});
const report={items:[],total:0,table_limit:300,summary:{candidates:0,stale:0,excess:0,decline:0},catalog_available:true,limitations:[]};
beforeEach(()=>{
  fetchMock=vi.fn(async (url:string,init?:RequestInit)=>{
    if(init?.method==='POST')return response(report);
    if(url.includes('/catalog-options/')){
      const values=url.includes('/brand')?Array.from({length:120},(_,i)=>`Бренд ${i}`):['Завод Альфа','Завод Бета'];
      const term=new URL(url,'http://test').searchParams.get('search')?.toLocaleLowerCase('ru')||'';
      return response(values.filter(value=>value.toLocaleLowerCase('ru').includes(term)));
    }
    if(url.includes('/filters'))return response({departments:[]});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
});
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
const posts=()=>fetchMock.mock.calls.filter(([,init])=>init?.method==='POST');

it('highlights only the selected period and clears it for manual dates',()=>{
  render(<PromotionCandidatesPage/>);
  const six=screen.getByRole('button',{name:'6 месяцев'}),three=screen.getByRole('button',{name:'3 месяцев'});
  expect(six.getAttribute('aria-pressed')).toBe('true');
  expect(six.classList.contains('active')).toBe(true);
  fireEvent.click(three);
  expect(three.classList.contains('active')).toBe(true);
  expect(six.getAttribute('aria-pressed')).toBe('false');
  fireEvent.change(screen.getByLabelText('Дата с'),{target:{value:'2026-01-01'}});
  expect(three.getAttribute('aria-pressed')).toBe('false');
  expect(posts()).toHaveLength(0);
});

it('searches inside full brand/manufacturer lists and sends single values only on Generate',async()=>{
  render(<PromotionCandidatesPage/>);
  expect(fetchMock.mock.calls.filter(([url])=>url.includes('/catalog-options/'))).toHaveLength(0);
  fireEvent.click(screen.getByRole('combobox',{name:'Бренд'}));
  await screen.findByRole('option',{name:'Бренд 119'});
  expect(within(screen.getByRole('listbox',{name:'Бренд: значения'})).getAllByRole('option')).toHaveLength(121);
  fireEvent.change(screen.getByRole('textbox',{name:'Поиск: Бренд'}),{target:{value:'БРЕНД 119'}});
  expect(screen.queryByRole('option',{name:'Бренд 1'})).toBeNull();
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>new URL(url,'http://test').searchParams.get('search')==='БРЕНД 119')).toBe(true));
  fireEvent.click(screen.getByRole('option',{name:'Бренд 119'}));
  expect(screen.queryByRole('textbox',{name:'Поиск: Бренд'})).toBeNull();
  fireEvent.click(screen.getByRole('combobox',{name:'Производитель'}));
  await screen.findByRole('option',{name:'Завод Бета'});
  fireEvent.change(screen.getByRole('textbox',{name:'Поиск: Производитель'}),{target:{value:'бета'}});
  expect(screen.queryByRole('option',{name:'Завод Альфа'})).toBeNull();
  fireEvent.click(screen.getByRole('option',{name:'Завод Бета'}));
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(posts()).toHaveLength(1));
  const payload=JSON.parse(posts()[0][1].body);
  expect(payload.brand).toBe('Бренд 119');expect(payload.manufacturer).toBe('Завод Бета');
});

it('supports clearing selection and Escape without triggering calculations',async()=>{
  render(<PromotionCandidatesPage/>);
  const trigger=screen.getByRole('combobox',{name:'Бренд'});
  fireEvent.click(trigger);fireEvent.click(await screen.findByRole('option',{name:'Бренд 1'}));
  fireEvent.click(trigger);fireEvent.click(screen.getByRole('button',{name:'Все бренды'}));
  expect(trigger.textContent).toContain('Все бренды');
  fireEvent.click(trigger);fireEvent.keyDown(screen.getByRole('textbox',{name:'Поиск: Бренд'}),{key:'Escape'});
  expect(trigger.getAttribute('aria-expanded')).toBe('false');
  expect(document.activeElement).toBe(trigger);expect(posts()).toHaveLength(0);
});

it('explains every visible criterion with an associated description',()=>{
  render(<PromotionCandidatesPage/>);
  fireEvent.click(screen.getByText('Критерии отбора'));
  for(const label of ['Падение, %','Минимум продаж ранее','Дней без продажи','Низкие продажи, шт.','Избыточный запас, мес.','Минимальный остаток Авиаторов Зал+Склад']){
    const input=screen.getByLabelText(label),id=input.getAttribute('aria-describedby');
    expect(id).toBeTruthy();expect(document.getElementById(id!)?.textContent?.length).toBeGreaterThan(40);
  }
  expect(posts()).toHaveLength(0);
});

it('shows list loading errors and only retries after explicit reopen',async()=>{
  const original=fetchMock.getMockImplementation()!;
  let calls=0;
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
    if(url.includes('/catalog-options/brand')){calls++;return Promise.resolve(calls===1?response({detail:'Unavailable'},503):response(['Бренд 1']))}
    return original(url,init);
  });
  render(<PromotionCandidatesPage/>);
  const trigger=screen.getByRole('combobox',{name:'Бренд'});
  fireEvent.click(trigger);
  await screen.findByRole('alert');
  expect(calls).toBe(1);expect(posts()).toHaveLength(0);
  fireEvent.click(trigger);fireEvent.click(trigger);
  await screen.findByRole('option',{name:'Бренд 1'});
  expect(calls).toBe(2);
});

it('seasonality is enabled by default and filter/toggle remain explicit report parameters',async()=>{
  render(<PromotionCandidatesPage/>);
  const toggle=screen.getByRole('checkbox',{name:'Учитывать сезонность'}) as HTMLInputElement;
  expect(toggle.checked).toBe(true);
  fireEvent.change(screen.getByLabelText('Сезонность'),{target:{value:'Скоро сезон'}});
  fireEvent.click(toggle);
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(posts()).toHaveLength(1));
  const payload=JSON.parse(posts()[0][1].body);
  expect(payload.use_seasonality).toBe(false);
  expect(payload.seasonality_status).toBe('Скоро сезон');
});

it('sends an empty named warehouse minimum as null only on Generate',async()=>{
  render(<PromotionCandidatesPage/>);
  fireEvent.change(screen.getByLabelText('Минимальный остаток Авиаторов Зал+Склад'),{target:{value:''}});
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await waitFor(()=>expect(posts()).toHaveLength(1));
  expect(JSON.parse(posts()[0][1].body).criteria.min_stock).toBeNull();
});
