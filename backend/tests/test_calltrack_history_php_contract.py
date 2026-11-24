"""Cross-language evidence contract: real PG rows API consumed by actual Calltrack PHP validator."""
import asyncio
import json
import os
import subprocess

from sqlalchemy import update
from app.models import Sale

from test_calltrack_identity_history import lab, summary, page


def php_advance(snapshot, payload, progress, after, *, should_pass=True):
    helper = os.environ.get("CALLTRACK_HISTORY_PHP_HELPER")
    assert helper and os.path.isfile(helper), "Calltrack PHP helper checkout required"
    code = """
    require_once $argv[1];
    $x=json_decode(stream_get_contents(STDIN),true,64,JSON_THROW_ON_ERROR);
    try {
      $out=analyticsHistoryAdvancePage($x['snapshot'],$x['page'],$x['progress'],$x['after']);
      echo json_encode($out,JSON_THROW_ON_ERROR);
    } catch (DomainException $e) {fwrite(STDERR,'rejected');exit(3);}
    """
    call = subprocess.run(
        ["php", "-r", code, helper],
        input=json.dumps(dict(snapshot=snapshot, page=payload, progress=progress, after=after),
                         ensure_ascii=False),
        capture_output=True, text=True, timeout=10, check=False,
    )
    if should_pass:
        assert call.returncode == 0, call.stderr
        return json.loads(call.stdout)
    assert call.returncode == 3, (call.returncode, call.stderr)
    return None


def test_real_pg_api_to_real_php_contract(lab):
    app, factory, _ = lab

    async def run():
        sresp = await summary(app)
        assert sresp.status_code == 200, sresp.text
        source = sresp.json()
        assert source["sale_count"] == 6
        progress = dict(history_hash=source["history_hash"], after=0, seen=0, complete=False)
        previous = 0
        received = []
        while True:
            response = await page(app, source["history_hash"], previous, size=2)
            assert response.status_code == 200, response.text
            body = response.json()
            progress = php_advance(source, body, progress, previous)
            received.extend(x["sale_id"] for x in body["items"])
            if progress["complete"]:
                break
            previous = progress["after"]
        assert received == [1, 2, 5, 6, 7, 8]
        assert progress["seen"] == source["sale_count"]
        # Cannot repeat a page after completion.
        php_advance(source, body, progress, previous, should_pass=False)
        # Any mutation invalidates the next API page, even with same row count.
        from decimal import Decimal
        async with factory() as db:
            await db.execute(update(Sale).where(Sale.id == 2)
                             .values(total_amount=Decimal("10.21")))
            await db.commit()
        assert (await page(app, source["history_hash"], after=0)).status_code == 409
        updated = (await summary(app)).json()
        assert updated["history_hash"] != source["history_hash"]
        # Mixed old/new JSON evidence never advances Calltrack session.
        fresh = (await page(app, updated["history_hash"], after=0)).json()
        php_advance(source, fresh,
                    dict(history_hash=source["history_hash"], after=0,
                         seen=0, complete=False), 0, should_pass=False)
    asyncio.run(run())
