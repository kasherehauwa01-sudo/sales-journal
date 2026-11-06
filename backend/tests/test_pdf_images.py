from contextlib import contextmanager
from io import BytesIO
import time
from urllib.error import HTTPError
from unittest.mock import Mock

from PIL import Image
import pytest
from app.services import pdf_images as images
from app.services.report_pdf import safe_document

URL = 'https://kvasmix.ru/vr/catalog/media/photo.png'

@pytest.mark.parametrize('url', [
 'http://kvasmix.ru/vr/catalog/media/x', 'https://other.example/media/x',
 'https://127.0.0.1/vr/catalog/media/x', 'https://[::1]/vr/catalog/media/x',
 'https://localhost/vr/catalog/media/x', 'https://user:password@kvasmix.ru/vr/catalog/media/x',
 'https://kvasmix.ru:444/vr/catalog/media/x', 'https://kvasmix.ru/vr/calltrack/media/x',
 'https://kvasmix.ru/vr/catalog/api/integration/x', 'file:///etc/passwd',
 'https://kvasmix.ru/vr/catalog/media/%2e%2e/api/x',
 'https://kvasmix.ru/vr/catalog/media/%252e%252e/api/x', 'javascript:alert(1)', '',
])
def test_allowlist_rejects_unknown_hosts_schemes_ports_credentials_and_traversal(url):
 assert images.allowed_image_url(url) is None


def test_static_catalog_url_is_allowed():
 assert images.allowed_image_url(URL) == URL


def png():
 out=BytesIO();Image.new('RGB',(20,20),'red').save(out,format='PNG');return out.getvalue()


def response(data,content_type='image/png'):
 stream=BytesIO(data);stream.headers={'Content-Type':content_type};return stream


def test_reuses_thumbnail_and_keeps_all_rows(monkeypatch,tmp_path):
 calls=[]
 @contextmanager
 def fetch(url,deadline):
  calls.append(url)
  with response(png()) as stream:yield stream
 monkeypatch.setattr(images,'image_response',fetch)
 loader=images.PdfImages(tmp_path)
 snapshot='<table>'+''.join(f'<tr><td>Товар-{i}</td><td><img data-pdf-image="{URL}"></td><td>{i}</td></tr>' for i in range(500))+'</table>'
 document=safe_document(snapshot,image_loader=loader)
 assert calls==[URL] and document.count('data:image/png;base64,')==500
 assert all(f'Товар-{i}' in document for i in range(500))
 assert 'data-pdf-image' not in document and URL not in document


@pytest.mark.parametrize('failure',['missing','mime','bytes','pixels','deadline','budget'])
def test_unavailable_images_are_placeholders_and_do_not_remove_data(monkeypatch,tmp_path,failure):
 @contextmanager
 def fetch(url,deadline):
  if failure=='missing':raise OSError('private address must never reach logs')
  data=png()
  if failure=='bytes':monkeypatch.setattr(images,'MAX_IMAGE_BYTES',1)
  if failure=='pixels':monkeypatch.setattr(images,'MAX_IMAGE_PIXELS',1)
  with response(data,'text/html' if failure=='mime' else 'image/png') as stream:yield stream
 monkeypatch.setattr(images,'image_response',fetch)
 loader=images.PdfImages(tmp_path)
 if failure=='deadline':loader.deadline=0
 if failure=='budget':loader.embedded_bytes=images.MAX_EMBEDDED_BYTES
 document=safe_document(f'<table><tr><td>Товар</td><td><img data-pdf-image="{URL}"></td><td>123.45</td></tr></table>',image_loader=loader)
 assert 'Фото недоступно' in document and 'Товар' in document and '123.45' in document
 assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('destination',['http://127.0.0.1/secret','https://evil.example/media/x','https://kvasmix.ru/vr/calltrack/media/x'])
def test_redirects_cannot_escape_catalog_allowlist(monkeypatch,destination):
 seen=[]
 def opener(*handlers):
  def open_(request,timeout):
   seen.append(request)
   raise HTTPError(request.full_url,302,'redirect',{'Location':destination},None)
  return Mock(open=open_)
 monkeypatch.setattr(images,'build_opener',opener)
 monkeypatch.setattr(images,'public_address',lambda _:('8.8.8.8',443))
 with pytest.raises(ValueError):
  with images.image_response(URL,time.perf_counter()+5):pass
 assert len(seen)==1
 assert set(seen[0].headers)=={'Accept','User-agent'}


def test_private_dns_answer_prevents_connection(monkeypatch):
 from app.services import promotion_selected_export as transport
 monkeypatch.setattr(transport.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
 connect=Mock();monkeypatch.setattr(images,'build_opener',connect)
 with pytest.raises(ValueError):
  with images.image_response(URL,time.perf_counter()+5):pass
 connect.assert_not_called()


def test_no_image_has_no_fetch(monkeypatch):
 fetch=Mock(side_effect=AssertionError('unexpected request'))
 monkeypatch.setattr(images,'image_response',fetch)
 assert 'Сумма 100' in images.image_document('<p>Сумма 100</p>')
 fetch.assert_not_called()


def test_repeated_images_count_towards_total_embedded_budget(monkeypatch,tmp_path):
 @contextmanager
 def fetch(url,deadline):
  with response(png()) as stream:yield stream
 monkeypatch.setattr(images,'image_response',fetch)
 loader=images.PdfImages(tmp_path)
 first=loader(URL)
 assert first
 monkeypatch.setattr(images,'MAX_EMBEDDED_BYTES',len(first)*2)
 assert loader(URL)==first
 assert loader(URL) is None


def test_failed_images_and_unique_limit_do_not_repeat_downloads(monkeypatch,tmp_path):
 calls=[]
 @contextmanager
 def fetch(url,deadline):
  calls.append(url)
  raise OSError('unavailable')
  yield
 monkeypatch.setattr(images,'image_response',fetch)
 monkeypatch.setattr(images,'MAX_UNIQUE_IMAGES',1)
 loader=images.PdfImages(tmp_path)
 assert loader(URL) is None and loader(URL) is None
 assert loader(URL+'?v=2') is None
 assert calls==[URL]


def test_memory_guard_is_not_swallowed_as_photo_failure(monkeypatch,tmp_path):
 from fastapi import HTTPException
 def low_memory():raise HTTPException(503,'memory_headroom')
 monkeypatch.setattr(images,'ensure_headroom',low_memory)
 with pytest.raises(HTTPException):images.PdfImages(tmp_path)(URL)


def test_busy_dns_worker_does_not_queue_more_requests(monkeypatch):
 connect=Mock();monkeypatch.setattr(images,'build_opener',connect)
 assert images.DNS_BUSY.acquire(blocking=False)
 try:
  with pytest.raises(ValueError,match='dns_busy'):
   with images.image_response(URL,time.perf_counter()+5):pass
 finally:
  images.DNS_BUSY.release()
 connect.assert_not_called()



def test_total_download_budget_stops_remaining_fetches(monkeypatch,tmp_path):
 calls=[]
 @contextmanager
 def fetch(url,deadline):
  calls.append(url)
  with response(png()) as stream:yield stream
 monkeypatch.setattr(images,'image_response',fetch)
 monkeypatch.setattr(images,'MAX_DOWNLOADED_BYTES',1)
 loader=images.PdfImages(tmp_path)
 assert loader(URL) is None and loader(URL+'?v=2') is None
 assert calls==[URL]



@pytest.mark.parametrize('address',['224.0.0.1','fec0::1','64:ff9b::7f00:1','2002:7f00:1::'])
def test_special_or_translated_addresses_never_connect(monkeypatch,address):
 monkeypatch.setattr(images,'public_address',lambda _:(address,443))
 connect=Mock();monkeypatch.setattr(images,'build_opener',connect)
 with pytest.raises(ValueError):
  with images.image_response(URL,time.perf_counter()+5):pass
 connect.assert_not_called()
