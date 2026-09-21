import {PeriodPicker,type Period} from './PeriodPicker';
import {ClearableInput} from './ClearableInput';
import {useApi} from '../hooks/useApi';
export type AFilters={period:Period;date_from:string;date_to:string;department:string;price_type:string};
export function AnalyticsFilters({value,onChange,showPriceType=false}:{value:AFilters;onChange:(x:AFilters)=>void;showPriceType?:boolean}){const {data}=useApi<{price_types:string[]}>('/filters');return <div className="analytics-filter"><PeriodPicker value={value} onChange={period=>onChange({...value,...period})}/><label>Подразделение<ClearableInput placeholder="Все подразделения" value={value.department} onChange={department=>onChange({...value,department})}/></label>{showPriceType&&<label>Тип цены<select value={value.price_type} onChange={event=>onChange({...value,price_type:event.target.value})}><option value="">Все типы цен</option>{data?.price_types.map(item=><option key={item} value={item}>{item}</option>)}</select></label>}</div>}
