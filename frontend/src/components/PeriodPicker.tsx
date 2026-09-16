import {CalendarDays,X} from 'lucide-react';
import {useEffect,useState} from 'react';

export type Period='day'|'week'|'month'|'quarter'|'year'|'all'|'custom';
export type PeriodValue={period:Period;date_from:string;date_to:string};

const periods:Array<[Period,string]>=[['day','День'],['week','Неделя'],['month','Месяц'],['quarter','Квартал'],['year','Год'],['all','Все'],['custom','Ручной выбор']];
const iso=(value:Date)=>`${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;

function range(period:Exclude<Period,'all'|'custom'>){
  const today=new Date();const from=new Date(today);const to=new Date(today);
  if(period==='week')from.setDate(today.getDate()-((today.getDay()+6)%7));
  if(period==='month')from.setDate(1);
  if(period==='quarter')from.setMonth(Math.floor(today.getMonth()/3)*3,1);
  if(period==='year')from.setMonth(0,1);
  return {date_from:iso(from),date_to:iso(to)};
}

export function PeriodPicker({value,onChange}:{value:PeriodValue;onChange:(value:PeriodValue)=>void}){
  const [manualOpen,setManualOpen]=useState(false);const [from,setFrom]=useState(value.date_from);const [to,setTo]=useState(value.date_to);
  useEffect(()=>{if(!manualOpen){setFrom(value.date_from);setTo(value.date_to)}},[value.date_from,value.date_to,manualOpen]);
  function select(period:Period){
    if(period==='custom'){setFrom(value.period==='custom'?value.date_from:'');setTo(value.period==='custom'?value.date_to:'');setManualOpen(true);return}
    onChange(period==='all'?{period,date_from:'',date_to:''}:{period,...range(period)});
  }
  function apply(){if(!from||!to||from>to)return;onChange({period:'custom',date_from:from,date_to:to});setManualOpen(false)}
  return <>
    <div className="period-picker" aria-label="Период">{periods.map(([period,label])=><button type="button" key={period} className={value.period===period?'active':''} onClick={()=>select(period)}>{period==='custom'&&<CalendarDays size={15}/>} {label}</button>)}</div>
    {manualOpen&&<><div className="backdrop" onClick={()=>setManualOpen(false)}/><div className="period-modal" role="dialog" aria-modal="true" aria-labelledby="period-title"><div className="period-modal-head"><div><h2 id="period-title">Ручной выбор периода</h2><p>Укажите начальную и конечную даты</p></div><button type="button" aria-label="Закрыть" onClick={()=>setManualOpen(false)}><X/></button></div><div className="period-modal-fields"><label>Дата от<input type="date" value={from} onChange={e=>setFrom(e.target.value)}/></label><label>Дата до<input type="date" value={to} min={from} onChange={e=>setTo(e.target.value)}/></label></div>{from&&to&&from>to&&<p className="period-error">Дата окончания не может быть раньше даты начала</p>}<div className="period-modal-actions"><button type="button" onClick={()=>setManualOpen(false)}>Отмена</button><button type="button" className="primary" disabled={!from||!to||from>to} onClick={apply}>Применить</button></div></div></>}
  </>
}
