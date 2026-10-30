import {useEffect, useRef, useState} from 'react';
import {API} from '../api/client';
import {stamp,valueText} from '../hooks/useChurnStatus';

type Config={time:string;daily_limit:number;cooldown_days:number;algorithm_policy:string;producer_policy_revision:number;receiver_policy_revision:number};
type State={policy:{revision:number;enabled:boolean;approved:boolean;settings:Partial<Config>};next_run:string|null;history:{id:string;status:string;kind:string;updated_at:string;error_code:string|null;counters:Record<string,number>}[]};
const names:Record<string,string>={QUEUED:'В очереди',CALCULATING:'Расчёт',AWAITING_EVIDENCE:'Ожидает независимого подтверждения источника',RELEASED:'Разрешено к передаче',DELIVERING:'Доставка',SUCCEEDED:'Приём подтверждён',PARTIAL:'Часть сигналов отклонена',BLOCKED:'Заблокировано',FAILED:'Ошибка: требуется проверка перед ручным повтором',DIAGNOSED:'Диагностика завершена без отправки',EXPIRED:'Устаревший запуск'};

export function ChurnAutomationControl(){
  const submitting=useRef(false);
  const [data,setData]=useState<State|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [form,setForm]=useState({time:'09:00',daily_limit:'',cooldown_days:'',producer_policy_revision:'',receiver_policy_revision:''});
  const [revision,setRevision]=useState(0);
  useEffect(()=>{const controller=new AbortController();setBusy(true);
    fetch(`${API}/settings/churn-scenario/automation`,{credentials:'same-origin',signal:controller.signal}).then(async r=>{if(!r.ok)throw Error('Не удалось прочитать постоянную автоматизацию. Проверьте backend и миграцию.');return r.json()}).then((s:State)=>{
      if(!s.policy||typeof s.policy.revision!=='number'||typeof s.policy.approved!=='boolean'||typeof s.policy.enabled!=='boolean'||!s.policy.settings||!Array.isArray(s.history)||s.history.some(j=>!j.counters||typeof j.status!=='string'))throw Error('Несовместимый ответ API');
      setData(s);const c=s.policy.settings;setForm({time:c.time??'09:00',daily_limit:String(c.daily_limit??''),cooldown_days:String(c.cooldown_days??''),producer_policy_revision:String(c.producer_policy_revision??''),receiver_policy_revision:String(c.receiver_policy_revision??'')});
    }).catch(e=>{if(!controller.signal.aborted)setError(e.message)}).finally(()=>{if(!controller.signal.aborted)setBusy(false)});
    return()=>controller.abort();
  },[revision]);
  async function command(action:string){if(busy||!data||submitting.current)return;submitting.current=true;setBusy(true);setError('');setNotice('');
    try{const settings=action==='draft'?{...form,daily_limit:Number(form.daily_limit),cooldown_days:Number(form.cooldown_days),producer_policy_revision:Number(form.producer_policy_revision),receiver_policy_revision:Number(form.receiver_policy_revision),algorithm_policy:'aviator-churn-pilot/2'}:undefined;
      const r=await fetch(`${API}/settings/churn-scenario/automation`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Churn-Control':'1'},body:JSON.stringify({action,revision:data.policy.revision,...(settings?{settings}:{})})});
      if(!r.ok)throw Error(r.status===409?'Изменение требует утверждения оператора или политика уже изменена.':'Не удалось сохранить. Обновите статус перед повторной попыткой.');
      setNotice(action==='diagnostic'?'Диагностика поставлена в очередь. Отправки и создания задач не будет. Обработает установленный host-worker.':'Изменение сохранено. Черновик требует отдельного утверждения оператором.');setRevision(v=>v+1);
    }catch(e){setError(e instanceof Error?e.message:'Ошибка управления')}finally{submitting.current=false;setBusy(false)}
  }
  return <section className="panel settings-panel"><h2>Постоянная автоматизация</h2>
    {error&&<p role="alert" className="filter-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {data&&<><p>Сценарий: <b>{data.policy.enabled?'Включён':'Выключен'}</b> · Политика: {data.policy.approved?'Утверждена':'Требует утверждения'} · Ревизия {data.policy.revision}</p>
      <p>Следующее время по политике: {stamp(data.next_run)}. Исполнение зависит от установленного systemd-таймера; это время не подтверждает его работоспособность.</p>
      <div className="filters-row">{([['time','Ежедневный запуск (Москва)','time'],['daily_limit','Новых задач в день','number'],['cooldown_days','Защита от повторного сигнала, дней','number'],['producer_policy_revision','Утверждённая ревизия допуска Sales Journal','number'],['receiver_policy_revision','Утверждённая ревизия Calltrack','number']] as const).map(([key,label,type])=><label key={key}>{label}<input type={type} min="1" value={form[key]} onChange={e=>setForm(v=>({...v,[key]:e.target.value}))}/></label>)}</div>
      <p>Сохранение черновика отключает сценарий и снимает утверждение. Утверждение выполняет оператор через защищённую CLI; оно само по себе не включает отправку.</p>
      <div className="scenario-actions"><button disabled={busy} onClick={()=>command('draft')}>Сохранить черновик</button><button disabled={busy||!data.policy.approved||data.policy.enabled} onClick={()=>command('enable')}>Включить утверждённый сценарий</button><button disabled={busy||!data.policy.enabled} onClick={()=>command('disable')}>Выключить</button><button disabled={busy} onClick={()=>command('diagnostic')}>Диагностика без создания задач</button><button disabled={busy} onClick={()=>{setError('');setRevision(v=>v+1)}}>Обновить историю</button></div>
      <h3>Расчёт → Подготовка → Допуск → Отправка → Приём → Задачи</h3>
      {!data.history.length?<p>Запусков ещё нет. Автоматический запуск после развёртывания выключен.</p>:<div className="table-wrap"><table><thead><tr><th>Время / режим</th><th>Этап</th><th>Рассчитано</th><th>Подготовлено</th><th>Разрешено</th><th>Отправлено</th><th>Принято</th><th>Созданы задачи</th><th>Отклонено</th></tr></thead><tbody>{data.history.map(j=><tr key={j.id}><td>{stamp(j.updated_at)} · {j.kind==='diagnostic'?'Диагностика':'Ежедневный'}</td><td><details><summary>{names[j.status]??j.status}</summary><p>ID запуска: {j.id}</p><p>{j.error_code??'Нет сохранённой ошибки'}</p><p>Связанных задач: {valueText(j.counters.tasks_linked)}</p></details></td>{['calculated','prepared','allowed','sent','accepted','tasks_created','rejected'].map(k=><td key={k}>{valueText(j.counters[k])}</td>)}</tr>)}</tbody></table></div>}
    </>}
  </section>
}
