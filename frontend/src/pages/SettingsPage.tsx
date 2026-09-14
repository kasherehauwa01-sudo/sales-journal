import {Check,Clipboard} from 'lucide-react';
import {useState} from 'react';

const UPDATE_SCRIPT = '/var/www/html/vr/update_sales.sh';

export function SettingsPage(){
  const [copied,setCopied]=useState(false);
  async function copyPath(){
    await navigator.clipboard.writeText(UPDATE_SCRIPT);
    setCopied(true);
    window.setTimeout(()=>setCopied(false),2000);
  }
  return <div className="page">
    <div className="page-title"><div><h1>Настройки</h1><p>Системные параметры и обслуживание сервиса</p></div></div>
    <section className="panel settings-panel">
      <h2>Обновление приложения</h2>
      <p className="muted">Нажмите на путь, чтобы скопировать его в буфер обмена.</p>
      <button className="copy-field" onClick={copyPath} title="Скопировать путь">
        <code>{UPDATE_SCRIPT}</code>
        {copied?<><Check/><span>Скопировано</span></>:<Clipboard/>}
      </button>
    </section>
  </div>
}
