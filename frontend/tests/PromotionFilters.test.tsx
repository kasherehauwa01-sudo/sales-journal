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
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals()});
const posts=()=>fetchMock.mock.calls.filter(([url,init])=>init?.method==='POST'&&!String(url).includes('/product-catalog'));

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

it('shows the same product photo in the report and opened card with a fallback on error',async()=>{
  const original=fetchMock.getMockImplementation()!;
  const item={key:'code:001',code:'001',name:'Товар с фото',category:'Посуда',image_url:'https://catalog.test/photo.jpg',reasons:[],stop_factors:[],status:'Кандидат',units:1,units_30:1,units_previous_30:1,units_60_90:1,revenue:10,stock:2,monthly_units:1};
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>init?.method==='POST'?Promise.resolve(response({...report,items:[item],total:1})):original(url,init));
  render(<PromotionCandidatesPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  const photo=await screen.findByRole('img',{name:'Товар с фото'});
  expect(photo.getAttribute('src')).toBe(item.image_url);
  expect(photo.getAttribute('loading')).toBe('lazy');
  fireEvent.click(screen.getByText('Товар с фото'));
  expect(screen.getAllByRole('img',{name:'Товар с фото'})).toHaveLength(2);
  fireEvent.error(screen.getAllByRole('img',{name:'Товар с фото'})[1]);
  expect(screen.getByLabelText('Нет фото: Товар с фото')).toBeTruthy();
  expect(posts()).toHaveLength(1);
});

it('persists column selection on this device and adds descriptions without recalculating',async()=>{
  const original=fetchMock.getMockImplementation()!;
  const item={key:'code:001',name:'Тест',category:'Посуда',reasons:[],stop_factors:[],status:'Кандидат',units_30:1,units_previous_30:1};
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>init?.method==='POST'?Promise.resolve(response({...report,items:[item],total:1})):original(url,init));
  const view=render(<PromotionCandidatesPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await screen.findByRole('button',{name:'Настройка колонок'});
  fireEvent.click(screen.getByRole('button',{name:'Настройка колонок'}));
  fireEvent.click(screen.getByRole('checkbox',{name:'30 дней'}));
  expect(screen.getByRole('button',{name:'Описание: 30 дней'}).getAttribute('title')).toContain('30 дней');
  expect(posts()).toHaveLength(1);
  view.unmount();
  render(<PromotionCandidatesPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await screen.findByRole('button',{name:'Настройка колонок'});
  expect(screen.getByRole('button',{name:'Описание: 30 дней'})).toBeTruthy();
});

it('loads warehouse balances and OZ manager only when opening the product card',async()=>{
  const original=fetchMock.getMockImplementation()!;
  const item={key:'code:001',code:'001',name:'Товар',category:'Посуда',reasons:[],stop_factors:[],status:'Кандидат',units_30:1,units_previous_30:1,units_60_90:1};
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
    if(url.includes('/product-catalog'))return Promise.resolve(response({stocks:[{name:'Склад А',quantity:7}],oz_manager:'Ответственный ОЗ'}));
    return init?.method==='POST'?Promise.resolve(response({...report,items:[item],total:1})):original(url,init);
  });
  render(<PromotionCandidatesPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  await screen.findByText('Товар',{selector:'b'});
  expect(fetchMock.mock.calls.filter(([url])=>url.includes('/product-catalog'))).toHaveLength(0);
  fireEvent.click(screen.getByText('Товар',{selector:'b'}));
  expect(await screen.findByText('Ответственный ОЗ')).toBeTruthy();
  expect(screen.getByText('Склад А')).toBeTruthy();
  expect(posts()).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(([url])=>url.includes('/product-catalog'))).toHaveLength(1);
});

it('selects visible rows, supports select all/clear all, and exports only the selected snapshot',async()=>{
  const original=fetchMock.getMockImplementation()!;
  const items=[1,2].map(i=>({key:`code:00${i}`,code:`00${i}`,name:`Товар ${i}`,category:'Посуда',reasons:[],stop_factors:[],status:'Кандидат',units_30:1,units_previous_30:1}));
  fetchMock.mockImplementation((url:string,init?:RequestInit)=>{
    if(url.includes('/export-selected'))return Promise.resolve(new Response(new Blob(['xlsx'])));
    return init?.method==='POST'?Promise.resolve(response({...report,items,total:2})):original(url,init);
  });
  const create=vi.fn(()=> 'blob:export'),revoke=vi.fn();
  vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:create,revokeObjectURL:revoke}));
  const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  render(<PromotionCandidatesPage/>);
  fireEvent.click(screen.getByRole('button',{name:'Сформировать отчёт'}));
  const first=await screen.findByRole('checkbox',{name:'Выбрать товар: Товар 1'}) as HTMLInputElement;
  expect(first.checked).toBe(false);
  expect(screen.queryByRole('button',{name:/Скачать XLS/})).toBeNull();
  fireEvent.click(first);
  expect(screen.queryByText('Остатки по складам')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Скачать XLS (1)'}));
  fireEvent.click(screen.getByRole('button',{name:'Скачать XLS (1)'}));
  await waitFor(()=>expect(fetchMock.mock.calls.filter(([url])=>url.includes('/export-selected'))).toHaveLength(1));
  const selectedCall=fetchMock.mock.calls.find(([url])=>url.includes('/export-selected'))!;
  expect(JSON.parse(selectedCall[1].body).rows.map((row:any)=>row.code)).toEqual(['001']);
  await waitFor(()=>expect(create).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('checkbox',{name:'Выделить все'}));
  expect(screen.getByRole('button',{name:'Скачать XLS (2)'})).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox',{name:'Снять все'}));
  expect(screen.queryByRole('button',{name:/Скачать XLS/})).toBeNull();
  click.mockRestore();
});
