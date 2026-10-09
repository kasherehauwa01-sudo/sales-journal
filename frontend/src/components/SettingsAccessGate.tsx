import {LockKeyhole,LogOut} from 'lucide-react';
import {FormEvent,useEffect,useState} from 'react';
import {Outlet} from 'react-router-dom';
import {api} from '../api/client';
import {Loading} from './States';

export function SettingsAccessGate(){
 const [state,setState]=useState<'loading'|'login'|'ready'>('loading');const [password,setPassword]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 useEffect(()=>{api('/settings/auth/session').then(()=>setState('ready')).catch(()=>setState('login'))},[]);
 async function login(event:FormEvent){event.preventDefault();setBusy(true);setError('');try{await api('/settings/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});setPassword('');setState('ready')}catch(exc){setError(exc instanceof Error?exc.message:'Не удалось войти')}finally{setBusy(false)}}
 async function logout(){await api('/settings/auth/logout',{method:'POST'});setState('login')}
 if(state==='loading')return <div className="page"><Loading/></div>;
 if(state==='login')return <div className="page settings-login-page"><section className="panel settings-login"><LockKeyhole size={34}/><h1>Доступ к настройкам</h1><p>Введите пароль администратора.</p><form onSubmit={login}><label>Пароль<input autoFocus type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)}/></label>{error&&<div className="error">{error}</div>}<button className="primary" disabled={busy||!password}>{busy?'Проверка…':'Войти'}</button></form></section></div>;
 return <><div className="settings-session"><button onClick={logout}><LogOut size={15}/>Выйти из настроек</button></div><Outlet/></>;
}
