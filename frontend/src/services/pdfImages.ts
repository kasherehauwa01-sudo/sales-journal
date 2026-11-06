// Product photos are best-effort. Never change their live DOM or drop report rows.
export const PHOTO_UNAVAILABLE = 'Фото недоступно';
export function photoUrl(value:string):string|undefined {
 try {
  if(/[\x00-\x1f\x7f\\]/.test(value)) return;
  const url=new URL(value,window.location.href);
  if(!['https:','http:'].includes(url.protocol)||url.username||url.password) return;
  return url.href;
 } catch {return;}
}
function raster(image:HTMLImageElement):string {
 if(!image.complete||!image.naturalWidth||!image.naturalHeight) throw new Error('image_not_ready');
 const scale=Math.min(1,192/image.naturalWidth,144/image.naturalHeight);
 const canvas=document.createElement('canvas');
 canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));
 canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
 const context=canvas.getContext('2d');if(!context) throw new Error('canvas_unavailable');
 context.drawImage(image,0,0,canvas.width,canvas.height);
 return canvas.toDataURL('image/png');
}
export function photoPlaceholder(image:Element) {
 const placeholder=document.createElement('span');
 placeholder.className=image.getAttribute('class')||'pdf-photo-unavailable';
 placeholder.setAttribute('style',image.getAttribute('style')||'');
 placeholder.textContent=PHOTO_UNAVAILABLE;
 image.replaceWith(placeholder);
}
export function createPhotoEncoder(signal:AbortSignal) {
 const cache=new Map<string,string|undefined>(),deadline=performance.now()+3000;
 let embedded=0;
 function include(data:string,copy:Element) {
  if(embedded+data.length>4*1024*1024) return false;
  embedded+=data.length;copy.setAttribute('src',data);return true;
 }
 return async (original:HTMLImageElement,copy:Element)=>{
  if(signal.aborted) throw new DOMException('Aborted','AbortError');
  const source=original.currentSrc||original.getAttribute('src')||'';
  if(cache.has(source)) {
   const data=cache.get(source);
   if(!data||!include(data,copy)) fallback();
   return;
  }
  function fallback() {
   const url=photoUrl(source);
   if(url) {copy.setAttribute('data-pdf-image',url);copy.removeAttribute('src');}
   else photoPlaceholder(copy);
  }
  if(cache.size>=500) {fallback();return;}
  try {const data=raster(original);cache.set(source,data);if(!include(data,copy)) fallback();return;} catch {/* CORS/broken/lazy photo */}
  const url=photoUrl(source);
  // A regular <img> is tainted even when its server supports CORS. Retry as an
  // anonymous image, without cross-origin credentials or referrer; bounded and sequential.
  if(url&&performance.now()<deadline) {
   const image=new Image();image.crossOrigin='anonymous';image.referrerPolicy='no-referrer';
   try {
    await new Promise<void>((resolve,reject)=>{
     const done=(error?:Error)=>{
      clearTimeout(timer);signal.removeEventListener('abort',abort);
      image.onload=null;image.onerror=null;
      if(error){image.removeAttribute('src');reject(error);} else resolve();
     };
     const abort=()=>done(new DOMException('Aborted','AbortError'));
     const timer=setTimeout(()=>done(new Error('photo_timeout')),Math.max(1,Math.min(350,deadline-performance.now())));
     signal.addEventListener('abort',abort,{once:true});
     image.onload=()=>done();image.onerror=()=>done(new Error('photo_unavailable'));image.src=url;
    });
    const data=raster(image);cache.set(source,data);if(!include(data,copy)) fallback();return;
   } catch {if(signal.aborted) throw new DOMException('Aborted','AbortError');}
  }
  cache.set(source,undefined);fallback();
 };
}
