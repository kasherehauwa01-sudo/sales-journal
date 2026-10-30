import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {ChurnScenarioPage} from '../src/pages/ChurnScenarioPage';
import {ChurnScenarioCard} from '../src/components/ChurnScenarioCard';
import {ScenariosPage} from '../src/pages/ScenariosPage';

const diagnostic={code:'ALGORITHM_POLICY_NOT_APPROVED',title:'Версия алгоритма не утверждена',component:'Sales Journal',explanation:'Требуется отдельное утверждение /2.',recommendation:'Проверить новую политику.',severity:'blocking',detected_at:'2026-10-30T07:00:00Z',impact:'Передача заблокирована.'};
const good={api_version:1,read_only:true,status:'ENABLED',observed_at:'2026-10-30T07:00:00Z',
  configuration:{automation_enabled:true,shadow_worker_enabled:false,calltrack_token_configured:true,mode:'SHADOW',delivery_mode:'AUTOMATION_APPROVED',active_algorithm:'aviator-churn-pilot/2',generator_version:'inactivity/aviator-churn-pilot/2'},
  policy:{enabled:true,approved:true,revision:4,algorithm_policy:'aviator-churn-pilot/2',min_confidence:.4,approved_at:null},
  calculations:{last_attempt:null,last_success:null,active_run_id:null},
  statistics:{scope:'Текущий расчёт и связанная публикация',analyzed_clients:10,risk_clients:3,ready_signals:3,published_signals:3,accepted_signals:2,blocked_signals:0,tasks_created:1,verified_bindings:10,preliminary_bindings:0,pending_bindings:0},
  systemd:{timer_enabled:true,timer_active:true,service_state:'inactive',service_result:'success',last_attempt:'2026-10-30T06:00:00Z',last_success:'2026-10-30T06:00:01Z',next_run:'2026-10-31T06:00:00Z',schedule:'09:00 Europe/Moscow'},
  runtime_observed_at:'2026-10-30T07:00:00Z',integrity:'match',receiver:{status:'available',policy_approved:true,min_confidence:.4,lifetime:{limit:10,used:2,remaining:8},daily:{limit:5,used:2,remaining:3}},
  publication:{id:'release-1',created_at:'2026-10-30T07:00:00Z'},diagnostics:[],history:[],identity_reviews:[]};
const show=(element:React.ReactNode)=>(render(<MemoryRouter>{element}</MemoryRouter>));
const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status});
afterEach(()=>{cleanup();vi.unstubAllGlobals()});

it('shows facts separately for SHADOW calculation, publication, accepted signals and tasks; GET only',async()=>{
  const fetch=vi.fn(async()=>json(good));vi.stubGlobal('fetch',fetch);show(<ChurnScenarioPage/>);
  await screen.findByText('Общее состояние — Включён');
  expect(screen.getByText('SHADOW')).toBeTruthy();expect(screen.getByText('AUTOMATION_APPROVED')).toBeTruthy();
  for(const label of ['Опубликовано сигналов','Приём Calltrack подтверждён','Связанных задач Calltrack'])expect(screen.getByText(label)).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button',{name:/Запустить|Утвердить|Отправить/})).toBeNull();
  expect(String(fetch.mock.calls[0][0])).toContain('/settings/churn-scenario/status');
});

it.each(['DISABLED','ERROR','NEEDS_SETUP'])("shows state %s and specific block code without mutations",async status=>{
  const fetch=vi.fn(async()=>json({...good,status,diagnostics:[diagnostic]}));vi.stubGlobal('fetch',fetch);show(<ChurnScenarioPage/>);
  await screen.findByText('ALGORITHM_POLICY_NOT_APPROVED');
  expect(screen.getByText('Версия алгоритма не утверждена')).toBeTruthy();
  expect(screen.getByText(/Проверить новую политику/)).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('keeps unknown values as Нет данных rather than zero and does not fabricate server status',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>json({...good,status:'NEEDS_SETUP',systemd:null,receiver:null,runtime_observed_at:null,
    statistics:{scope:'Нет подтверждения',analyzed_clients:null,published_signals:3,accepted_signals:null,tasks_created:null},diagnostics:[diagnostic]})));
  show(<ChurnScenarioPage/>);await screen.findByText('Общее состояние — Требует настройки');
  const accepted=screen.getByText('Приём Calltrack подтверждён').parentElement!;
  expect(within(accepted).getByText('Нет данных')).toBeTruthy();
  const next=screen.getByText('Следующий запуск').parentElement!;
  expect(within(next).getByText('Нет данных')).toBeTruthy();
});

it('provides card links for details and separate churn history',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>json(good)));show(<ChurnScenarioCard/>);
  await screen.findByText('Включён');
  expect(screen.getByRole('link',{name:'Подробнее'}).getAttribute('href')).toBe('/settings/scenarios/churn-risk');
  expect(screen.getByRole('link',{name:'История'}).getAttribute('href')).toBe('/settings/scenarios/churn-risk#history');
});

it('handles old backend API without hiding the existing HoReCa scenario',async()=>{
  const fetch=vi.fn(async(url:unknown)=>String(url).includes('/churn-scenario/')?json({},404):json([
    {id:1,name:'Продажи HoReCa',email:'synthetic@example.test',reply_emails:'reply@example.test',manager:'Synthetic',message_text:'',enabled:true}]));
  vi.stubGlobal('fetch',fetch);show(<ScenariosPage/>);
  await screen.findByText('Продажи HoReCa');
  await screen.findByText(/Обновите backend Sales Journal/);
  fireEvent.click(screen.getByRole('button',{name:'Тест'}));
  expect(screen.getByRole('heading',{name:'Тестовая отправка'})).toBeTruthy();
  expect(screen.getByRole('button',{name:'Отправить'})).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(2); // Opening an email confirmation never sends it.
});

it('reports 503 without automatic retries and allows a manual status refresh',async()=>{
  const fetch=vi.fn().mockResolvedValueOnce(json({},503)).mockResolvedValueOnce(json(good));vi.stubGlobal('fetch',fetch);show(<ChurnScenarioPage/>);
  await screen.findByRole('alert');
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button',{name:'Обновить статус'}));
  await screen.findByText('Общее состояние — Включён');
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('rejects malformed or incompatible API responses without a render crash',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>json({api_version:1,read_only:true,status:'ENABLED',configuration:{},diagnostics:[],history:[]})));
  show(<ChurnScenarioPage/>);await screen.findByText(/Версия API диагностики несовместима/);
});

it('reports missing admin authentication',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>json({},401)));show(<ChurnScenarioPage/>);
  await screen.findByText('Требуется вход администратора настроек.');
});

it('shows bounded history details with missing delivery and does not fetch or send on expansion',async()=>{
  const fetch=vi.fn(async()=>json({...good,history:[{id:'external-1',component:'Внешний пилот',date:'2026-10-30T06:00:00Z',result:'blocked',clients:null,signals:null,delivered:null,diagnostics:[{...diagnostic,code:'LIFETIME_BUDGET_EXHAUSTED',title:'Общий лимит пилота исчерпан'}],details:{}}]}));
  vi.stubGlobal('fetch',fetch);show(<ChurnScenarioPage/>);
  await screen.findByText('Общий лимит пилота исчерпан',{selector:'summary'});
  fireEvent.click(screen.getByText('Общий лимит пилота исчерпан',{selector:'summary'}));
  expect(screen.getByText('LIFETIME_BUDGET_EXHAUSTED')).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('aborts status reads when the page unmounts',()=>{
  const fetch=vi.fn(()=>new Promise<Response>(()=>{}));vi.stubGlobal('fetch',fetch);
  const {unmount}=show(<ChurnScenarioPage/>);const signal=(fetch.mock.calls as unknown[][])[0][1] as RequestInit;
  unmount();expect(signal.signal?.aborted).toBe(true);
});

it('shows a structured safe DB diagnostic rather than raw exception details',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>json({detail:{code:'DB_STATUS_UNAVAILABLE',explanation:'SECRET must not be reflected'}},503)));
  show(<ChurnScenarioPage/>);await screen.findByText(/DB_STATUS_UNAVAILABLE/);
  expect(screen.queryByText(/SECRET/)).toBeNull();
});
