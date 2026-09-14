import {AlertTriangle,X} from 'lucide-react';
import type {ImportBatch} from '../types';
import {datetime,number} from '../utils/format';

export function ImportErrorModal({item,onClose}:{item:ImportBatch;onClose:()=>void}){
  return <>
    <div className="backdrop" onClick={onClose}/>
    <section className="error-modal" role="dialog" aria-modal="true" aria-labelledby="import-error-title">
      <div className="drawer-head">
        <div><small>Импорт #{item.id}</small><h2 id="import-error-title"><AlertTriangle/> Детали ошибки</h2></div>
        <button onClick={onClose} aria-label="Закрыть"><X/></button>
      </div>
      <div className="error-modal-body">
        <div className="field-grid">
          <div className="field"><span>Файл</span><b>{item.filename}</b></div>
          <div className="field"><span>Дата запуска</span><b>{datetime(item.uploaded_at)}</b></div>
          <div className="field"><span>Обработано строк</span><b>{number(item.processed_rows)} из {number(item.total_rows)}</b></div>
        </div>
        {item.error_text&&<><h3>Критическая ошибка</h3><div className="error">{item.error_text}</div></>}
        <h3>Ошибки строк <span className="count">{item.errors.length}</span></h3>
        {item.errors.length?<div className="table-wrap"><table><thead><tr><th>Строка</th><th>Описание</th></tr></thead><tbody>{item.errors.map(error=><tr key={error.id}><td>{error.row_number??'—'}</td><td className="wrap-cell">{error.message}</td></tr>)}</tbody></table></div>:<p className="muted">Построчных ошибок нет. Причина указана в общем сообщении или логе.</p>}
        <h3>Лог импорта</h3>
        <pre className="import-log">{item.log_text||item.error_text||'Подробный лог для этого импорта отсутствует.'}</pre>
      </div>
    </section>
  </>
}
