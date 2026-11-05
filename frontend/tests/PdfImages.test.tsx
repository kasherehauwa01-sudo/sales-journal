import {afterEach,expect,it,vi} from 'vitest';
import {createPhotoEncoder,photoUrl} from '../src/services/pdfImages';
afterEach(()=>vi.restoreAllMocks());
function photo(src='https://catalog.example/photo.png') {
 const image=document.createElement('img');image.src=src;
 Object.defineProperties(image,{complete:{value:true},naturalWidth:{value:800},naturalHeight:{value:600}});
 return image;
}
function canvas(fail=false) {
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(()=>({drawImage:vi.fn()}) as any);
 vi.spyOn(HTMLCanvasElement.prototype,'toDataURL').mockImplementation(()=>{if(fail) throw new DOMException('tainted','SecurityError');return 'data:image/png;base64,AAAA'});
}
it('retains readable images with bounded raster dimensions',async()=>{
 canvas();const original=photo('/same-origin.png'),copy=original.cloneNode() as Element;
 await createPhotoEncoder(new AbortController().signal)(original,copy);
 expect(copy.getAttribute('src')).toBe('data:image/png;base64,AAAA');
 expect(original.getAttribute('src')).toBe('/same-origin.png');
});
it('routes tainted photos to constrained backend fallback without throwing',async()=>{
 canvas(true);vi.useFakeTimers();
 try {
  const original=photo(),copy=original.cloneNode() as Element;
  const task=createPhotoEncoder(new AbortController().signal)(original,copy);
  await vi.advanceTimersByTimeAsync(400);await task;
  expect(copy.getAttribute('data-pdf-image')).toBe(original.src);
  expect(copy.hasAttribute('src')).toBe(false);
 } finally {vi.useRealTimers()}
});
it('replaces invalid URLs without losing the row or values',async()=>{
 canvas(true);const row=document.createElement('tr');row.innerHTML='<td>Товар</td><td><img></td><td>123.45</td>';
 await createPhotoEncoder(new AbortController().signal)(photo('file:///etc/passwd'),row.querySelector('img')!);
 expect(row.textContent).toBe('ТоварФото недоступно123.45');
});
it.each(['javascript:alert(1)','file:///etc/passwd','https://user:pass@example.com/a','http://[invalid','https://x/\\secret'])('rejects invalid photo URL %s',value=>{
 expect(photoUrl(value)).toBeUndefined();
});
it('abort still cancels preparation instead of treating cancellation as a photo failure',async()=>{
 const controller=new AbortController();controller.abort();
 await expect(createPhotoEncoder(controller.signal)(photo(),document.createElement('img'))).rejects.toThrow('Aborted');
});
