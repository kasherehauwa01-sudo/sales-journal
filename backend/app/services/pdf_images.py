"""Bounded CatalogVR-only image embedding; no credentials or browser network."""
import base64
import ipaddress
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urljoin, unquote
from urllib.request import Request, ProxyHandler, build_opener

from PIL import Image, UnidentifiedImageError
from app.config import settings
from app.services.product_analytics_runtime import ensure_headroom
from app.services.promotion_selected_export import (
    trusted_image_url, public_address, PinnedHTTP, PinnedHTTPS, NoRedirect,
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS,
)

IMAGE_SECONDS = 12
MAX_UNIQUE_IMAGES = 500
MAX_DOWNLOADED_BYTES = 16 * 1024 * 1024
MAX_EMBEDDED_BYTES = 4 * 1024 * 1024
DNS_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pdf-image-dns')
DNS_BUSY = Lock()
STATIC_FOLDERS = ('media/', 'uploads/', 'static/', 'images/', 'storage/')


def allowed_image_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        url = trusted_image_url(value)
        if not url:
            return None
        actual, configured = urlsplit(url), urlsplit(settings.vrcatalog_api_url)
        if actual.scheme != 'https' or configured.scheme != 'https':
            return None
        if actual.hostname != configured.hostname or (actual.port or 443) != (configured.port or 443):
            return None
        root = configured.path.rstrip('/')
        if root.endswith('/api'):
            root = root[:-4]
        path = unquote(actual.path)
        if '%' in path or '\\' in path or any(part in ('.', '..') for part in path.split('/')):
            return None
        # Public static media only: never proxy integration APIs/other projects.
        if not any(path.startswith(root + '/' + folder) for folder in STATIC_FOLDERS):
            return None
        return url
    except (ValueError, TypeError):
        return None


@contextmanager
def image_response(url, deadline):
    # Redirects are manual, and every hop is checked against the same allowlist.
    for _ in range(3):
        url = allowed_image_url(url)
        if not url or time.perf_counter() >= deadline:
            raise ValueError('image_unavailable')
        # A stuck resolver must not build an unbounded queue of future lookups.
        if not DNS_BUSY.acquire(blocking=False):
            raise ValueError('image_dns_busy')
        def resolve():
            try:
                address = public_address(url)
                ip = ipaddress.ip_address(address[0])
                if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip, 'is_site_local', False):
                    raise ValueError('non_public_image_address')
                if ip.version == 6 and (ip.sixtofour or ip.teredo or ip in ipaddress.ip_network('64:ff9b::/96') or ip in ipaddress.ip_network('64:ff9b:1::/48')):
                    raise ValueError('translated_image_address')
                return address
            finally:
                DNS_BUSY.release()
        try:
            lookup = DNS_WORKER.submit(resolve)
        except RuntimeError:
            DNS_BUSY.release()
            raise ValueError('image_dns_unavailable') from None
        try:
            address = lookup.result(timeout=min(1, max(0.01, deadline-time.perf_counter())))
        finally:
            if lookup.cancel():  # never started: release its reservation here
                DNS_BUSY.release()
        # all DNS answers public; the actual connection is pinned to this address
        timeout = min(2, max(0.1, deadline - time.perf_counter()))
        request = Request(url, headers={'Accept': 'image/*', 'User-Agent': 'SalesJournal-PDF'})
        try:
            response = build_opener(ProxyHandler({}), PinnedHTTP(address), PinnedHTTPS(address), NoRedirect()).open(request, timeout=timeout)
        except HTTPError as error:
            location = error.headers.get('Location') if error.headers else None
            code = error.code
            error.close()
            if code not in (301, 302, 303, 307, 308) or not location:
                raise
            url = urljoin(url, location)
            continue
        with response:
            yield response
        return
    raise ValueError('image_redirect_limit')


class PdfImages:
    def __init__(self, directory):
        self.directory = directory
        self.deadline = time.perf_counter() + IMAGE_SECONDS
        self.cache = {}
        self.embedded_bytes = 0
        self.downloaded_bytes = 0

    def include(self, value):
        if not value or self.embedded_bytes + len(value) > MAX_EMBEDDED_BYTES:
            return None
        self.embedded_bytes += len(value)  # include repeated occurrences, not only unique URLs
        return value

    def __call__(self, source):
        url = allowed_image_url(source)
        if not url:
            return None
        if url in self.cache:
            return self.include(self.cache[url])
        if len(self.cache) >= MAX_UNIQUE_IMAGES or self.embedded_bytes >= MAX_EMBEDDED_BYTES or self.downloaded_bytes >= MAX_DOWNLOADED_BYTES or time.perf_counter() >= self.deadline:
            return None
        self.cache[url] = None
        raw = Path(self.directory) / 'source-image'
        try:
            ensure_headroom()
            with image_response(url, self.deadline) as response, raw.open('wb') as output:
                content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
                if content_type not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
                    return None
                size = 0
                while True:
                    ensure_headroom()
                    if time.perf_counter() >= self.deadline:
                        return None
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    self.downloaded_bytes += len(chunk)
                    if size > MAX_IMAGE_BYTES or self.downloaded_bytes > MAX_DOWNLOADED_BYTES:
                        return None
                    output.write(chunk)
            ensure_headroom()
            with Image.open(raw) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    return None
                image.thumbnail((192, 144))
                if image.mode not in ('RGB', 'RGBA'):
                    image = image.convert('RGB')
                buffer = BytesIO()
                image.save(buffer, format='PNG')
            data = buffer.getvalue()
            result = 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
            self.cache[url] = result
            return self.include(result)
        except (OSError, ValueError, HTTPError, URLError, UnidentifiedImageError, Image.DecompressionBombError):
            return None
        finally:
            raw.unlink(missing_ok=True)


def image_document(snapshot):
    from app.services.report_pdf import safe_document
    with tempfile.TemporaryDirectory(prefix='sales-pdf-images-') as directory:
        return safe_document(snapshot, image_loader=PdfImages(directory))
