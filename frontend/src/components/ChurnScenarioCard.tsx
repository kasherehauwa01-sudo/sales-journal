import {Link} from 'react-router-dom';
import {stamp,statusNames,useChurnStatus} from '../hooks/useChurnStatus';
const stages:Record<string,string>={QUEUED:'В очереди',CALCULATING:'Расчёт',AWAITING_EVIDENCE:'Ожидает подтверждения источника',RELEASED:'Разрешено',DELIVERING:'Доставка',SUCCEEDED:'Приём подтверждён',PARTIAL:'Частичный приём',FAILED:'Ошибка',BLOCKED:'Заблокировано',EXPIRED:'Устарел'};

export function ChurnScenarioCard(){
  const {data,loading,error,refresh}=useChurnStatus();
  const permanent=data?.permanent?.policy.revision?data.permanent:null;
  const last=permanent?.history.find(j=>j.kind==='daily');
  return <section className="panel churn-scenario-card" aria-labelledby="churn-scenario-title">
    <div><h2 id="churn-scenario-title">Сигнал Риск ухода</h2><p>Автоматический анализ риска ухода клиентов и передача сигналов в Calltrack</p></div>
    {loading?<p role="status">Читаем состояние…</p>:error?<p role="alert" className="filter-error">{error}</p>:data&&<>
      <p><strong>{permanent?(permanent.policy.enabled?'Постоянный сценарий включён':permanent.policy.approved?'Постоянная политика утверждена; сценарий выключен':'Постоянная политика требует утверждения'):statusNames[data.status]}</strong></p>
      <p className="muted">{permanent?`Последний этап постоянного сценария: ${last?stages[last.status]??'Нет подтверждения':'Нет запусков'} · ${stamp(last?.updated_at)} · Следующее время по политике: ${stamp(permanent.next_run)} (работоспособность таймера проверяется отдельно)`:`Последняя попытка пилота: ${stamp(data.systemd?.last_attempt)} · Следующий запуск: ${stamp(data.systemd?.next_run)}`}</p>
      {!data.runtime_observed_at&&<p className="muted">Состояние серверного процесса пока не подтверждено.</p>}
    </>}
    <div className="actions"><Link className="churn-link" to="/settings/scenarios/churn-risk">Подробнее</Link><Link className="churn-link" to="/settings/scenarios/churn-risk#history">История</Link>{error&&<button onClick={refresh}>Обновить статус</button>}</div>
  </section>;
}
