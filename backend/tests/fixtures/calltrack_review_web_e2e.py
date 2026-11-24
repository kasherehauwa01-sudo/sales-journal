"""Exercise actual PHP HTTP review/session/MariaDB against real FastAPI/PostgreSQL.
Disposable CI services only; never accepts a production URL or database.
"""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
STORE=Path("/tmp/calltrack-history-e2e")
assert os.environ.get("CALLTRACK_E2E_FIXTURE") == "1"
assert os.environ.get("ANALYTICS_TASK_TEST_DSN") == "mysql:host=analytics-test-mariadb;dbname=analytics_http_test"
assert os.environ["CALLTRACK_HISTORY_TEST_DATABASE_URL"].endswith(
    "@127.0.0.1:5432/calltrack_history_test")
STORE.mkdir(parents=True,exist_ok=True)
env=os.environ.copy()
env["CALLTRACK_STORAGE_DIR"]=str(STORE)
env["SALES_JOURNAL_BASE_URL"]="http://127.0.0.1:18080"
env["CALLTRACK_INTEGRATION_TOKEN"]="synthetic-token"
env["CALLTRACK_DB_HOST"]="analytics-test-mariadb"
env["CALLTRACK_DB_NAME"]="analytics_http_test"
env["CALLTRACK_DB_USER"]="root"
env["CALLTRACK_DB_PASS"]="test"

def req(path,body=None,cookie="",csrf="",method=None):
    data=(json.dumps(body,ensure_ascii=False).encode("utf-8") if body is not None else None)
    headers={"Accept":"application/json"}
    if data is not None:headers["Content-Type"]="application/json"
    if cookie:headers["Cookie"]="calltrack_web="+cookie
    if csrf:headers["X-CSRF-Token"]=csrf
    request=urllib.request.Request("http://127.0.0.1:18081"+path,
        data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=40) as response:
            return response.status,json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code,json.loads(exc.read())

def ready(url):
    for _ in range(80):
        try:
            urllib.request.urlopen(url,timeout=1)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403,404,422):return
            time.sleep(0.3)
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("Service did not start: "+url)

sales=subprocess.Popen(["python","backend/tests/fixtures/calltrack_review_source.py"],
    env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
php=None
try:
    try:
        ready("http://127.0.0.1:18080/api/integrations/calltrack/analytics/identity-history?client_key=x")
    except RuntimeError:
        if sales.poll() is None:
            sales.terminate()
        try:
            output=sales.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            sales.kill();output=sales.communicate()[0]
        print("Sales fixture output:",output,flush=True)
        raise
    seed=subprocess.run(["php","backend/tests/fixtures/calltrack_review_seed.php"],
        env=env,capture_output=True,text=True,timeout=30)
    assert seed.returncode==0,(seed.stdout,seed.stderr)
    values=json.loads(seed.stdout)
    key=urllib.parse.quote(values["signal_key"])
    cookie=values["session_id"]
    csrf=values["csrf"]
    php=subprocess.Popen(["php","-d",f"session.save_path={STORE}/sessions","-S",
        "127.0.0.1:18081","-t","calltrack"],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    ready("http://127.0.0.1:18081/api/analytics_verified_review.php")
    path=f"/api/analytics_verified_review.php?signal_key={key}"
    status,_=req(path)
    assert status==401,("Unauthenticated access",status)
    status,data=req(path,cookie=cookie)
    assert status==200,(status,data)
    assert data["history"]["sale_count"]==55
    assert data["group"]["reasons"]==[],data["group"]
    post=lambda payload:req("/api/analytics_verified_review.php",body=dict(signal_key=values["signal_key"],**payload),
                             cookie=cookie,csrf=csrf)
    status,data=post({"action":"confirm","expected_revision":0,
        "client_id":"synthetic-client-100","confirm_all_history":True,"confirm_identity":True})
    assert status==422,("Confirm before pages should fail",status,data)
    status,data=post({"action":"begin","expected_revision":0,"reason":"Synthetic CI full review"})
    assert status==200,(status,data)
    status,data=post({"action":"history_rows","after":0})
    assert status==200,(status,data)
    assert len(data["history"]["items"])==50 and not data["all_history_loaded"]
    after=data["history"]["next_after"]
    status,data=post({"action":"history_rows","after":after})
    assert status==200,(status,data)
    assert len(data["history"]["items"])==5 and data["all_history_loaded"]
    status,data=post({"action":"confirm","expected_revision":1,
        "client_id":"synthetic-client-100","confirm_all_history":True,"confirm_identity":True,
        "reason":"Synthetic evidence reconciled"})
    assert status==200 and data["review"]["status"]=="VERIFIED",(status,data)
    probe=subprocess.run(["php","-r",
        "require 'calltrack/api/config.php';$p=getPdo();echo $p->query(\"SELECT COUNT(*) FROM analytics_client_bindings WHERE status='VERIFIED'\")->fetchColumn();"],
        env=env,capture_output=True,text=True,timeout=10)
    assert probe.returncode==0 and int(probe.stdout.strip())>=1,(probe.stdout,probe.stderr)
    mutation=urllib.request.Request("http://127.0.0.1:18080/__synthetic_mutation",
        data=b"{}",method="POST",headers={"X-Test-Secret":"ci-local-only"})
    with urllib.request.urlopen(mutation,timeout=10) as response:
        assert json.load(response)["ok"]
    status,data=post({"action":"export"})
    assert status!=200,("Stale verified receipt must not export",status,data)
    print("WEB_E2E_PASS: unauthenticated rejected, admin session/CSRF, 55 source sales, two pages, MariaDB VERIFIED, stale evidence blocked",flush=True)
finally:
    for proc in [php,sales]:
        if proc is not None:
            proc.terminate()
            try:proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.communicate()
