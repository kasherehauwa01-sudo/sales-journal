import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {AnalyticsSignalsPage} from '../src/pages/AnalyticsSignalsPage';

afterEach(()=>{cleanup();vi.unstubAllGlobals()});
const result={mode:'SHADOW',automation_allowed:false,enabled:false,active_run:null,
 run:{id:'run',as_of:'2026-10-19',stale:false,coverage_status:'UNKNOWN',freshness:{status:'HEALTHY',reason:'RECENT_COMPLETED_IMPORT'},duration_seconds:1.5,generator_version:'inactivity/1',diagnostics:{eligible:12,not_eligible:3,multiple_phones:2}},
 total:80,next_after:'next-page',page_size:50,items:[{analytical_client_key:'ооо альфа',display_name:'ООО Альфа',eligible:true,reason:'ELIGIBLE',status:'ACTIVE',signal_type:'PURCHASE_OVERDUE',score:'65.00',confidence:'0.7500',features:{known_phones:[],median_interval:5,mean_interval:8,days_without_purchase:8,overdue_threshold:8,churn_threshold:15},signal:{detected_at:'2026-10-20'}}]};

it('shows shadow evidence and unknown coverage; filters and pagination are server-side GET only',async()=>{
 const fetch=vi.fn(async()=>new Response(JSON.stringify(result)));
 vi.stubGlobal('fetch',fetch);render(<AnalyticsSignalsPage/>);
 await screen.findByText('ООО Альфа');
 expect(screen.getByText('Только наблюдение · Автоматизация выключена')).toBeTruthy();
 expect(screen.getByText('UNKNOWN')).toBeTruthy();
 expect(screen.getByRole('button',{name:'Назад'}).hasAttribute('disabled')).toBe(true);
 expect(fetch.mock.calls).toHaveLength(1);
 const first=String((fetch.mock.calls as unknown[][])[0][0]);
 expect(first).not.toContain('eligible=');
 fireEvent.change(screen.getByLabelText('Клиент'),{target:{value:'Альфа'}});
 fireEvent.change(screen.getByLabelText('Тип'),{target:{value:'CHURN_RISK'}});
 expect(fetch.mock.calls).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'Применить'}));
 await waitFor(()=>expect(fetch.mock.calls).toHaveLength(2));
 expect(String((fetch.mock.calls as unknown[][])[1][0])).toContain('signal_type=CHURN_RISK');
 await screen.findByText('ООО Альфа');
 fireEvent.click(screen.getByRole('button',{name:'Далее'}));
 await waitFor(()=>expect(fetch.mock.calls).toHaveLength(3));
 expect(String((fetch.mock.calls as unknown[][])[2][0])).toContain('after=next-page');
 for(const call of fetch.mock.calls as unknown[][]){expect(call[1]).not.toHaveProperty('method','POST')}
});

it('explains disabled/no run and reports upstream failure',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({...result,run:null,items:[],total:0}))));
 render(<AnalyticsSignalsPage/>);
 await screen.findByText('Успешных shadow-запусков пока нет. Worker по умолчанию отключён.');
 cleanup();
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:'Требуется пароль администратора'}),{status:401})));
 render(<AnalyticsSignalsPage/>);
 await screen.findByText('Требуется пароль администратора');
});

const source={requested_as_of:'2026-10-19',effective_as_of:'2026-10-06',last_closed_business_day:'2026-10-19',
 declared_data_through:'2026-10-06',observed_data_through:'2026-10-05',accepted_data_through:'2026-10-06',
 business_freshness:'STALE',coverage_status:'DECLARED',coverage_method:'DECLARED_REPORT_PERIOD',
 reason_codes:['REQUESTED_DATE_AFTER_AVAILABLE_DATA'],shadow_allowed:true,automation_allowed:false,
 source_scope_limitation:'No completeness guarantee',
 evidence:[{id:1,filename:'sales.html',imported_at:'2026-10-20T09:00:00+03:00',declared_start:'2026-07-01',declared_end:'2026-10-06'}]};

it('explains stale historical cutoff, declared coverage, and business scope',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({...result,source_decision:source,
 scope:{department:'Авиаторов',buyer_types:['Оптовик','Корпоративный']}}))));
 render(<AnalyticsSignalsPage/>);
 const block=await screen.findByRole('region',{name:'Доступность данных'});
 expect(block.textContent).toContain('2026-10-19');expect(block.textContent).toContain('2026-10-06');
 expect(block.textContent).toContain('2026-10-05');expect(block.textContent).toContain('STALE');
 expect(block.textContent).toContain('DECLARED_REPORT_PERIOD');
 expect(block.textContent).toContain('полнота всех документов не подтверждена');
 expect(block.textContent).toContain('Разрешён исторический расчёт');
 expect(screen.getByText(/Подразделение: Авиаторов/).textContent).toContain('Оптовик, Корпоративный');
});

it('shows blocked unhealthy diagnostics even without a published run',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({...result,run:null,items:[],total:0,
 source_decision:{...source,coverage_status:'UNHEALTHY',business_freshness:'UNHEALTHY',shadow_allowed:false,
 effective_as_of:null,accepted_data_through:null,reason_codes:['OBSERVED_OUTSIDE_DECLARED_PERIOD']}}))));
 render(<AnalyticsSignalsPage/>);
 expect((await screen.findByRole('alert')).textContent).toContain('Новый расчёт заблокирован');
 expect(screen.getByRole('region',{name:'Доступность данных'}).textContent).toContain('OBSERVED_OUTSIDE_DECLARED_PERIOD');
});
