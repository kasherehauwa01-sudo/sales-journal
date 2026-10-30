import {useEffect} from 'react';
import {Link,useLocation} from 'react-router-dom';
import {Diagnostic,stamp,statusNames,useChurnStatus,valueText} from '../hooks/useChurnStatus';

const runNames:Record<string,string>={RUNNING:'Работает',SUCCEEDED:'Успешно',FAILED:'Ошибка',BLOCKED:'Заблокировано',PAUSED:'Приостановлено',SUPERSEDED:'Заменён новым расчётом',success:'Успешное завершение процесса',failed:'Ошибка',blocked:'Заблокировано',unknown:'Нет подтверждённого результата'};
function Metrics({rows}:{rows:[string,unknown][]}){return <dl className="catalog-sync-metrics">{rows.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{valueText(value)}</dd></div>)}</dl>}
function Problems({items}:{items:Diagnostic[]}){return <div className="churn-problems">{items.map((d,i)=><article key={`${d.code}-${i}`} className="churn-problem"><h3>{d.title}</h3><p><code>{d.code}</code> · {d.component} · {stamp(d.detected_at)}</p><p>{d.explanation}</p><p>{d.impact}</p><p><b>Как устранить:</b> {d.recommendation}</p></article>)}</div>}
export function ChurnScenarioPage(){
  const {data,loading,error,refresh}=useChurnStatus();
  const {hash}=useLocation();
  useEffect(()=>{if(data && hash==='#history'){const block=document.getElementById('history');block?.scrollIntoView?.();block?.focus()}},[data,hash]);
  return <div className="page churn-scenario-page"><div className="page-title"><div><h1>Сигнал Риск ухода</h1><p>Настройки → Сценарии · мониторинг существующего процесса</p></div><button disabled={loading} onClick={refresh}>Обновить статус</button></div>
    <Link to="/settings/scenarios">← Все сценарии</Link>
    <p className="muted">Эта страница только читает состояние. Она не запускает расчёт, не отправляет сигналы и не меняет разрешения. Время — по Москве.</p>
    {loading&&<p role="status">Загрузка состояния…</p>}{error&&<div className="filter-error" role="alert">{error}</div>}
    {data&&<>
      <section className="panel settings-panel"><h2>Общее состояние — {statusNames[data.status]}</h2>
        <Metrics rows={[
          ['Автоматизация в конфигурации',data.configuration.automation_enabled],['Режим аналитического расчёта',data.configuration.mode],['Режим разрешённой публикации',data.configuration.delivery_mode],
          ['Активная версия алгоритма',data.configuration.active_algorithm],['Утверждённая версия алгоритма',data.policy.algorithm_policy],
          ['Встроенный SHADOW-worker включён',data.configuration.shadow_worker_enabled],['Ежедневный таймер включён',data.systemd?.timer_enabled],
          ['Состояние systemd-сервиса',data.systemd?.service_state],['Результат systemd-сервиса',data.systemd?.service_result],
          ['Последняя попытка пилота',stamp(data.systemd?.last_attempt)],['Последнее успешное завершение пилота',stamp(data.systemd?.last_success)],
          ['Последняя попытка расчёта',stamp(data.calculations.last_attempt)],['Последний успешный расчёт',stamp(data.calculations.last_success)],
          ['Следующий запуск',stamp(data.systemd?.next_run)],['Наблюдение серверного процесса',stamp(data.runtime_observed_at)],
          ['Последняя ошибка',data.diagnostics.find(d=>d.severity==='error')?.title??(data.diagnostics.length?'См. блокировки ниже':'Нет подтверждённых ошибок')],
        ]}/><p className="muted">Внешний systemd-пилот и встроенный SHADOW-worker — разные источники состояния. Выключенный встроенный worker сам по себе не доказывает остановку внешнего таймера.</p>
      </section>
      <section className="panel settings-panel"><h2>Сигналы и клиенты</h2><p className="muted">{data.statistics.scope}. Публикация не равна доставке, а число сигналов не равно числу задач.</p>
        <Metrics rows={[
          ['Проанализировано клиентов',data.statistics.analyzed_clients],['Клиентов с риском ухода',data.statistics.risk_clients],
          ['Готово по последней проверке допуска',data.statistics.ready_signals],['Дата проверки допуска',stamp(data.statistics.decision_at as string|null)],['Опубликовано сигналов',data.statistics.published_signals],
          ['Приём Calltrack подтверждён',data.statistics.accepted_signals],['Заблокировано в Sales Journal',data.statistics.blocked_signals],
          ['Заблокировано в Calltrack',data.statistics.receiver_blocked_signals],['Связанных задач Calltrack',data.statistics.tasks_created],
          ['Подтверждённых привязок (VERIFIED)',data.statistics.verified_bindings],['Предварительных привязок',data.statistics.preliminary_bindings],
          ['Ожидают проверки',data.statistics.pending_bindings],
        ]}/>
        {!!data.identity_reviews.length&&<details><summary>Причины блокировки привязок (первые 50)</summary><div className="table-wrap"><table><thead><tr><th>ID клиента</th><th>ID менеджера</th><th>Статус</th><th>Причина</th></tr></thead><tbody>{data.identity_reviews.map((r,i)=><tr key={`${r.client_id}-${r.revision}-${i}`}><td>{r.client_id}</td><td>{r.manager_id}</td><td>{r.status}</td><td>{r.reason}</td></tr>)}</tbody></table></div></details>}
      </section>
      <section className="panel settings-panel"><h2>Диагностика ошибок и блокировок</h2>{data.diagnostics.length?<Problems items={data.diagnostics}/>:<p>В доступных источниках блокировки не обнаружены. Это не разрешение на создание задач.</p>}</section>
      <section className="panel settings-panel" id="history" tabIndex={-1}><h2>История работы</h2><p className="muted">Сохранённые расчёты и наблюдаемые запуски пилота показаны раздельно. Неизвестные показатели не подменяются нулём.</p>
        {!data.history.length?<p>История недоступна или запусков ещё нет.</p>:<div className="table-wrap"><table><thead><tr><th>Дата</th><th>Компонент</th><th>Результат</th><th>Клиенты</th><th>Сигналы</th><th>Принято</th><th>Ошибка / подробности</th></tr></thead><tbody>{data.history.map(r=><tr key={`${r.component}-${r.id}`}><td>{stamp(r.date)}</td><td>{r.component}</td><td>{runNames[r.result]??'Неизвестно'}</td><td>{valueText(r.clients)}</td><td>{valueText(r.signals)}</td><td>{valueText(r.delivered)}</td><td><details><summary>{r.diagnostics.length?r.diagnostics[0].title:'Подробнее'}</summary><Metrics rows={[
          ['Дата среза',r.details.as_of],['Генератор',r.details.generator],['Публикация',r.details.release_id],['Опубликовано',r.details.published],
        ]}/><Problems items={r.diagnostics}/></details></td></tr>)}</tbody></table></div>}
      </section>
      <section className="panel settings-panel"><h2>Настройки и ограничения</h2><Metrics rows={[
        ['Автоматизация включена',data.configuration.automation_enabled],['Расписание',data.systemd?.schedule],['Политика утверждена',data.policy.approved],
        ['Ревизия политики',data.policy.revision],['Дата утверждения',stamp(data.policy.approved_at)],['Порог уверенности Sales Journal',data.policy.min_confidence],
        ['Порог уверенности Calltrack',data.receiver?.min_confidence],['Тип CHURN_RISK включён',data.receiver?.churn_enabled],['Приоритет задач Calltrack',data.receiver?.priority],['Срок задач Calltrack, дней',data.receiver?.due_days],['Общий лимит задач',data.receiver?.lifetime?.limit],['Общий лимит использован',data.receiver?.lifetime?.used],
        ['Общий лимит остался',data.receiver?.lifetime?.remaining],['Дневной лимит задач',data.receiver?.daily?.limit],['Дневной лимит использован',data.receiver?.daily?.used],
        ['Дневной лимит остался',data.receiver?.daily?.remaining],['Дата дневного бюджета',data.receiver?.daily?.business_date],['Версия политики бюджета',data.receiver?.daily?.policy_version],
      ]}/>{!!data.receiver?.manager_budgets?.length&&<Metrics rows={data.receiver.manager_budgets.flatMap(b=>[[`Менеджер ${b.manager_id}: лимит за весь пилот`,b.limit],[`Менеджер ${b.manager_id}: использовано`,b.used],[`Менеджер ${b.manager_id}: осталось`,b.remaining]] as [string,unknown][])} />}<p>Изменение политики, confidence, привязок и лимитов выполняется только существующими административными механизмами с явным утверждением. Массового подтверждения и сброса счётчиков здесь нет.</p><Link to="/reports/analytics-signals">Открыть существующую диагностику аналитических сигналов</Link></section>
    </>}
  </div>;
}
