import {useEffect,useState} from 'react';

export function ProductPhoto({src,name,large=false}:{src?:string|null;name:string;large?:boolean}) {
  const [failed,setFailed]=useState(false);
  useEffect(()=>setFailed(false),[src]);
  const className=large?'product-card-photo':'analytics-product-photo';
  return !src || failed ? <span className={`${className} analytics-photo-placeholder`} aria-label={`Нет фото: ${name}`}>Нет фото</span> :
    <img className={className} src={src} alt={name} loading="lazy" decoding="async" onError={()=>setFailed(true)}/>;
}
