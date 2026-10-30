import {Link} from 'react-router-dom';
import {stamp,statusNames,useChurnStatus} from '../hooks/useChurnStatus';

export function ChurnScenarioCard(){
  const {data,loading,error,refresh}=useChurnStatus();
  return <section className="panel churn-scenario-card" aria-labelledby="churn-scenario-title">
    <div><h2 id="churn-scenario-title">Сигнал Риск ухода</h2><p>Автоматический анализ риска ухода клиентов и передача сигналов в Calltrack</p></div>
    {loading?<p role="status">Читаем состояние…</p>:error?<p role="alert" className="filter-error">{error}</p>:data&&<>
      <p><strong>{statusNames[data.status]}</strong></p>
      <p className="muted">Последняя попытка пилота: {stamp(data.systemd?.last_attempt)} · Следующий запуск: {stamp(data.systemd?.next_run)}</p>
      {!data.runtime_observed_at&&<p className="muted">Состояние серверного процесса пока не подтверждено.</p>}
    </>}
    <div className="actions"><Link className="churn-link" to="/settings/scenarios/churn-risk">Подробнее</Link><Link className="churn-link" to="/settings/scenarios/churn-risk#history">История</Link>{error&&<button onClick={refresh}>Обновить статус</button>}</div>
  </section>;
}
