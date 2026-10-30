import {useEffect, useState} from 'react';
import {API} from '../api/client';

export type Diagnostic = {code:string;title:string;component:string;explanation:string;recommendation:string;severity:string;detected_at:string|null;impact:string};
type Budget = {limit:number|null;used:number|null;remaining:number|null;business_date?:string|null;policy_version?:string|null};
export type ChurnStatus = {
  api_version:1;read_only:true;status:'ENABLED'|'DISABLED'|'ERROR'|'NEEDS_SETUP';observed_at:string;
  configuration:{automation_enabled:boolean;shadow_worker_enabled:boolean;calltrack_token_configured:boolean;mode:string;delivery_mode:string|null;active_algorithm:string;generator_version:string};
  policy:{enabled?:boolean;approved?:boolean;revision?:number;algorithm_policy?:string|null;min_confidence?:number|null;approved_at:string|null};
  calculations:{last_attempt:string|null;last_success:string|null;active_run_id:string|null};
  statistics:Record<string,number|string|null>;
  systemd:{timer_enabled:boolean|null;timer_active:boolean|null;service_state:string;service_result:string;last_attempt:string|null;last_success:string|null;next_run:string|null;schedule:string|null}|null;
  runtime_observed_at:string|null;integrity:string|null;
  receiver:{status:string;policy_approved:boolean|null;churn_enabled?:boolean|null;priority?:string|null;due_days?:number|null;min_confidence:number|null;lifetime:Budget|null;daily:Budget|null;manager_budgets?:(Budget&{manager_id:number})[]}|null;
  publication:{id:string;created_at:string}|null;
  diagnostics:Diagnostic[];
  history:{id:string;component:string;date:string;result:string;clients:number|null;signals:number|null;delivered:number|null;diagnostics:Diagnostic[];details:{as_of?:string;generator?:string;release_id?:string|null;published?:number|null}}[];
  identity_reviews:{client_id:string;manager_id:number;status:string;revision:number;reason:string}[];
};
export const statusNames:Record<ChurnStatus['status'],string> = {ENABLED:'Включён',DISABLED:'Отключён',ERROR:'Ошибка',NEEDS_SETUP:'Требует настройки'};
export const stamp = (value:string|null|undefined) => value ? new Date(value).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}) : 'Нет данных';
export const valueText = (value:unknown) => value === null || value === undefined ? 'Нет данных' : typeof value === 'boolean' ? value ? 'Да' : 'Нет' : String(value);

async function readStatus(signal:AbortSignal):Promise<ChurnStatus> {
  const response = await fetch(`${API}/settings/churn-scenario/status`,{signal,credentials:'same-origin',headers:{Accept:'application/json'}});
  if(response.status===404 || response.status===405) throw new Error('Обновите backend Sales Journal: API диагностики сценария ещё не установлен.');
  if(response.status===401 || response.status===403) throw new Error('Требуется вход администратора настроек.');
  if(!response.ok){
    let code='';try{code=(await response.json())?.detail?.code??''}catch{}
    if(code==='DB_STATUS_UNAVAILABLE')throw new Error('Не удалось прочитать состояние Sales Journal (DB_STATUS_UNAVAILABLE). Проверьте соединение с БД и совместимость backend; автоматизация не запускалась.');
    throw new Error(`Не удалось прочитать состояние сценария (HTTP ${response.status}). Автоматизация не запускалась. Повторите чтение статуса.`);
  }
  const data = await response.json();
  if(data?.api_version!==1 || data?.read_only!==true || !Object.hasOwn(statusNames,data.status) || !['configuration','policy','calculations','statistics'].every(key=>data[key] && typeof data[key]==='object' && !Array.isArray(data[key])) || !['diagnostics','history','identity_reviews'].every(key=>Array.isArray(data[key]))) {
    throw new Error('Версия API диагностики несовместима. Обновите backend и frontend согласованно.');
  }
  return data;
}
export function useChurnStatus() {
  const [revision,setRevision]=useState(0);
  const [data,setData]=useState<ChurnStatus|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    setLoading(true);setError('');setData(null);
    readStatus(controller.signal).then(result=>{if(!controller.signal.aborted)setData(result)}).catch(exc=>{
      if(!controller.signal.aborted)setError(exc instanceof Error?exc.message:'Статус недоступен');
    }).finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[revision]);
  return {data,loading,error,refresh:()=>setRevision(v=>v+1)};
}
