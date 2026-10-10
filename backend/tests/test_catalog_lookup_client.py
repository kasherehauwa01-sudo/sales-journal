import asyncio
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

import pytest
from app.services import catalog_lookup_client as client


def product(**overrides):
    return dict(
        product_id=1,
        code="001",
        article="DUP",
        manufacturer="Завод",
        brand="Бренд",
        category_id=7,
        category="Родитель",
        subcategory="Раздел",
        legacy_category="Раздел",
        material="Фарфор",
        horeca=False,
        updated_at="2026-10-10T08:00:00.123456Z",
        **overrides
    )


def response(items):
    return {
        "schema_version": 1,
        "items": [
            {
                "request_index": i,
                "status": "matched",
                "matched_by": "code",
                "product": product(),
            }
            for i in range(len(items))
        ],
    }


def test_transport_authorization_path_timeout_and_leading_zeros(monkeypatch):
    seen = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == client.MAX_RESPONSE_BYTES + 1
            return json.dumps(response([1])).encode()

    def open_url(request, timeout):
        seen.append(request)
        assert timeout == client.TIMEOUT_SECONDS
        assert request.headers["Authorization"] == "Bearer TEST_SECRET"
        assert request.full_url.endswith("/integration/products/analytics/lookup")
        assert json.loads(request.data) == {"items": [{"code": "001"}]}
        return Reply()

    monkeypatch.setattr(client.settings, "vrcatalog_api_token", "TEST_SECRET")
    monkeypatch.setattr(client, "urlopen", open_url)
    results = asyncio.run(client.lookup_products([{"code": "001"}]))
    assert results[0].product.code == "001"
    assert results[0].product.updated_at.microsecond == 123456
    assert len(seen) == 1


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"code": "x"}] * 251,
        [{"code": 1}],
        [{"product_id": True}],
        [{"code": " "}],
        [{"code": "x" * 129}],
    ],
)
def test_invalid_input_never_calls_transport(monkeypatch, items):
    monkeypatch.setattr(client, "_request", lambda *_: pytest.fail("transport called"))
    with pytest.raises(client.LookupError):
        asyncio.run(client.lookup_products(items))


@pytest.mark.parametrize("status", ["not_found", "ambiguous"])
def test_nonmatched_results(status, monkeypatch):
    monkeypatch.setattr(
        client,
        "_request",
        lambda items: {
            "schema_version": 1,
            "items": [
                {
                    "request_index": 0,
                    "status": status,
                    "matched_by": None,
                    "product": None,
                }
            ],
        },
    )
    result = asyncio.run(client.lookup_products([{"article": "DUP"}]))
    assert result[0].status == status and result[0].product is None


@pytest.mark.parametrize(
    "failure",
    [
        client.LookupError("transport_error", True),
        client.LookupError("http_500", True),
        client.LookupError("http_403"),
    ],
)
def test_bounded_retries_and_safe_logs(monkeypatch, caplog, failure):
    calls = []

    def fail(items):
        calls.append(1)
        raise failure

    async def sleep(delay):
        assert delay in {0.5, 1.0}

    monkeypatch.setattr(client, "_request", fail)
    monkeypatch.setattr(client.asyncio, "sleep", sleep)
    with pytest.raises(client.LookupError):
        asyncio.run(client.lookup_products([{"code": "SECRET_SKU"}]))
    assert len(calls) == (3 if failure.retryable else 1)
    assert "SECRET_SKU" not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "items": []},
        {
            "schema_version": 1,
            "items": [
                {
                    "request_index": 1,
                    "status": "not_found",
                    "matched_by": None,
                    "product": None,
                }
            ],
        },
        {
            "schema_version": 1,
            "items": [
                {
                    "request_index": 0,
                    "status": "matched",
                    "matched_by": None,
                    "product": None,
                }
            ],
        },
    ],
)
def test_invalid_response_is_not_retried(monkeypatch, payload):
    calls = []

    def fetch(items):
        calls.append(1)
        return payload

    monkeypatch.setattr(client, "_request", fetch)
    with pytest.raises(client.LookupError, match="invalid_response"):
        asyncio.run(client.lookup_products([{"code": "001"}]))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "exc",
    [
        HTTPError("secret-url", 403, "secret", {}, None),
        HTTPError("secret-url", 500, "secret", {}, None),
        URLError("secret"),
        TimeoutError("secret"),
    ],
)
def test_transport_errors_are_redacted(monkeypatch, exc):
    monkeypatch.setattr(client.settings, "vrcatalog_api_token", "TEST_SECRET")

    def fail(*args, **kwargs):
        raise exc

    monkeypatch.setattr(client, "urlopen", fail)
    with pytest.raises(client.LookupError) as error:
        client._request([{"code": "001"}])
    assert "secret" not in str(error.value).lower()


def test_client_serializes_calls_and_waits_for_cancelled_thread(monkeypatch):
    import threading

    release = threading.Event()
    entered = threading.Event()
    active = 0
    peak = 0

    def request(items):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        release.wait(2)
        active -= 1
        return response(items)

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "_slot", asyncio.Semaphore(1))

    async def run():
        first = asyncio.create_task(client.lookup_products([{"code": "001"}]))
        await asyncio.to_thread(entered.wait, 1)
        second = asyncio.create_task(client.lookup_products([{"code": "001"}]))
        first.cancel()
        await asyncio.sleep(0.01)
        assert not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second

    asyncio.run(run())
    assert peak == 1


def test_known_product_id_never_falls_back_to_other_product(monkeypatch):
    monkeypatch.setattr(
        client,
        "_request",
        lambda items: {
            "schema_version": 1,
            "items": [
                {
                    "request_index": 0,
                    "status": "not_found",
                    "matched_by": None,
                    "product": None,
                }
            ],
        },
    )
    assert (
        asyncio.run(
            client.lookup_products([{"product_id": 123, "code": "001", "article": "A"}])
        )[0].status
        == "not_found"
    )
    bad = response([1])
    bad["items"][0]["matched_by"] = "product_id"
    monkeypatch.setattr(client, "_request", lambda items: bad)
    with pytest.raises(client.LookupError, match="invalid_response"):
        asyncio.run(client.lookup_products([{"product_id": 123, "code": "001"}]))


def test_oversized_response_is_rejected_without_truncation(monkeypatch):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b"x" * (client.MAX_RESPONSE_BYTES + 1)

    monkeypatch.setattr(client.settings, "vrcatalog_api_token", "TEST_SECRET")
    monkeypatch.setattr(client, "urlopen", lambda *args, **kwargs: Reply())
    with pytest.raises(client.LookupError, match="response_too_large"):
        client._request([{"code": "001"}])


def test_retry_after_long_wait_stops_for_operator(monkeypatch):
    def fail(*args, **kwargs):
        raise HTTPError("redacted", 429, "redacted", {"Retry-After": "60"}, None)

    monkeypatch.setattr(client.settings, "vrcatalog_api_token", "TEST_SECRET")
    monkeypatch.setattr(client, "urlopen", fail)
    with pytest.raises(client.LookupError) as error:
        client._request([{"code": "001"}])
    assert not error.value.retryable and error.value.retry_after == 60
