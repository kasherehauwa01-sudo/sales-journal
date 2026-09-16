import {PeriodPicker,type Period} from './PeriodPicker';
export type AFilters={period:Period;date_from:string;date_to:string;department:string};
export function AnalyticsFilters({value,onChange}:{value:AFilters;onChange:(x:AFilters)=>void}){return <div className="analytics-filter"><PeriodPicker value={value} onChange={period=>onChange({...value,...period})}/><label>Подразделение<input placeholder="Все подразделения" value={value.department} onChange={e=>onChange({...value,department:e.target.value})}/></label></div>}
