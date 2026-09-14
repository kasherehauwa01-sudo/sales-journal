export const Loading=()=> <div className="loading"><i/><i/><i/><span>Загрузка данных…</span></div>;
export const Empty=({text='Данных пока нет'}:{text?:string})=><div className="empty"><b>{text}</b><span>Измените фильтры или загрузите файл продаж.</span></div>;
export const ErrorBox=({text}:{text:string})=><div className="error">{text}</div>;
