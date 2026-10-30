import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {ChurnAutomationControl} from '../src/components/ChurnAutomationControl';

const config={time:'09:00',daily_limit:5,cooldown_days:30,algorithm_policy:'aviator-churn-pilot/2',producer_policy_revision:4,receiver_policy_revision:2};
const state={policy:{revision:2,approved:true,enabled:false,settings:config},next_run:null,history:[]};
const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status});
afterEach(()=>{cleanup();vi.unstubAllGlobals()});

it('reads without starting automation and keeps unknown delivery distinct from zero',async()=>{
 const fetch=vi.fn(async()=>json({...state,history:[{id:'job',kind:'daily',status:'RELEASED',updated_at:'2026-10-30T07:00:00Z',counters:{calculated:3,prepared:3,allowed:2}}]}));vi.stubGlobal('fetch',fetch);
 render(<ChurnAutomationControl/>);await screen.findByText('Разрешено к передаче');
 expect(fetch).toHaveBeenCalledTimes(1);expect(fetch.mock.calls[0][0]).toContain('/automation');
 expect(screen.getAllByText('Нет данных').length).toBeGreaterThan(3);
 expect(screen.queryByRole('button',{name:/Утвердить/})).toBeNull();
});

it('cannot enable an unapproved draft',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>json({...state,policy:{...state.policy,approved:false}})));render(<ChurnAutomationControl/>);
 await screen.findByText(/Требует утверждения/);
 expect((screen.getByRole('button',{name:'Включить утверждённый сценарий'}) as HTMLButtonElement).disabled).toBe(true);
});

it('draft is explicit and versioned with csrf header; no approval request',async()=>{
 const fetch=vi.fn(async(_url:unknown,options?:RequestInit)=>options?.method==='POST'?json({revision:3}):json(state));vi.stubGlobal('fetch',fetch);
 render(<ChurnAutomationControl/>);await screen.findByText(/Ревизия 2/);
 fireEvent.change(screen.getByLabelText('Новых задач в день'),{target:{value:'7'}});
 fireEvent.click(screen.getByRole('button',{name:'Сохранить черновик'}));
 await screen.findByText(/Изменение сохранено/);
 const call=fetch.mock.calls.find(c=>c[1]?.method==='POST')!;
 expect(JSON.parse(String(call[1]?.body))).toEqual({action:'draft',revision:2,settings:{...config,daily_limit:7}});
 expect(call[1]?.headers).toEqual({'Content-Type':'application/json','X-Churn-Control':'1'});
});

it('diagnostic queues once while request pending and never calls delivery',async()=>{
 let finish:(v:Response)=>void=()=>{};
 const fetch=vi.fn(async(_url:unknown,options?:RequestInit)=>options?.method==='POST'?new Promise<Response>(r=>{finish=r}):json(state));vi.stubGlobal('fetch',fetch);
 render(<ChurnAutomationControl/>);await screen.findByText(/Ревизия 2/);
 const button=screen.getByRole('button',{name:'Диагностика без создания задач'});fireEvent.click(button);fireEvent.click(button);
 expect(fetch.mock.calls.filter(c=>c[1]?.method==='POST')).toHaveLength(1);
 finish(json({job_id:'job',status:'QUEUED'}));await screen.findByText(/Диагностика поставлена/);
 expect(JSON.parse(String(fetch.mock.calls.find(c=>c[1]?.method==='POST')![1]?.body)).action).toBe('diagnostic');
});

it('503 does not retry mutations automatically and offers status refresh',async()=>{
 const fetch=vi.fn(async(_url:unknown,options?:RequestInit)=>options?.method==='POST'?json({},503):json(state));vi.stubGlobal('fetch',fetch);
 render(<ChurnAutomationControl/>);await screen.findByText(/Ревизия 2/);
 fireEvent.click(screen.getByRole('button',{name:'Включить утверждённый сценарий'}));await screen.findByRole('alert');
 expect(fetch.mock.calls.filter(c=>c[1]?.method==='POST')).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'Обновить историю'}));await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(3));
});

it('rejects incompatible status without crashing or displaying fabricated counters',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>json({policy:{},history:[{}]})));render(<ChurnAutomationControl/>);
 await screen.findByText('Несовместимый ответ API');expect(screen.queryByText('Приём подтверждён')).toBeNull();
});
