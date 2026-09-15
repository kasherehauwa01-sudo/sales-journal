export const money=(v:string|number|null|undefined)=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',minimumFractionDigits:2}).format(Number(v||0));
export const number=(v:string|number|null|undefined)=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(v||0));
export const percent=(v:string|number|null|undefined)=>`${number(v)}%`;
export const date=(v?:string)=>v?new Intl.DateTimeFormat('ru-RU').format(new Date(`${v.slice(0,10)}T00:00:00`)):'—';
export const datetime=(v?:string)=>v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—';
