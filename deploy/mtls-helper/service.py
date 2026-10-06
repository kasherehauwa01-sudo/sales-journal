#!/usr/bin/env python3
"""Минимальный host-side RPC для выпуска и отзыва сертификатов Sales Journal."""
import base64,hashlib,hmac,json,os,re,secrets,shutil,socketserver,subprocess,tempfile,threading,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(os.environ.get("SALES_MTLS_DIR","/etc/nginx/sales-mtls"));SOCKET=Path(os.environ.get("SALES_MTLS_SOCKET","/run/sales-mtls-helper/helper.sock"))
PACKAGES=Path(os.environ.get("SALES_MTLS_PACKAGES","/var/lib/sales-mtls-helper/packages"));SECRET=os.environ.get("SALES_MTLS_HELPER_SECRET","")
CONFIG=ROOT/"openssl.cnf";CA_CERT=ROOT/"ca.crt";CA_KEY=ROOT/"ca.key";CRL=ROOT/"crl.pem";CERTS=ROOT/"issued"
CN_RE=re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

def run(args,*,env=None):
 result=subprocess.run(args,capture_output=True,text=True,env=env,timeout=60)
 if result.returncode:raise RuntimeError((result.stderr or result.stdout or "Ошибка команды").strip()[:1000])
 return result.stdout
def iso_date(value):return datetime.strptime(value.strip(),"%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc).isoformat()
def cert_info(path):
 text=run(["openssl","x509","-in",str(path),"-noout","-serial","-fingerprint","-sha256","-startdate","-enddate","-subject","-nameopt","RFC2253"]);values={}
 for line in text.splitlines():
  if "=" in line:
   key,value=line.split("=",1);values[key.lower()]=value
 subject=values.get("subject","");match=re.search(r"(?:^|,)CN=([^,]+)",subject)
 if not match:raise RuntimeError("В сертификате отсутствует CN")
 return {"common_name":match.group(1),"serial_number":values["serial"].upper(),"fingerprint":values["sha256 fingerprint"].replace(":","").upper(),"created_at":iso_date(values["notbefore"]),"expires_at":iso_date(values["notafter"])}
def package_paths(serial):
 safe=re.sub(r"[^A-Fa-f0-9]","",serial)
 if not safe:raise RuntimeError("Некорректный serial number")
 return PACKAGES/f"{safe}.p12",PACKAGES/f"{safe}.json"
def issue(payload):
 cn=payload.get("common_name","");days=int(payload.get("validity_days",1095))
 if not CN_RE.fullmatch(cn) or not 1<=days<=1825:raise RuntimeError("Некорректные параметры сертификата")
 with tempfile.TemporaryDirectory(prefix="sales-mtls-") as directory:
  work=Path(directory);key=work/"device.key";csr=work/"device.csr";cert=work/"device.crt"
  run(["openssl","genpkey","-algorithm","RSA","-pkeyopt","rsa_keygen_bits:3072","-out",str(key)])
  run(["openssl","req","-new","-key",str(key),"-out",str(csr),"-subj",f"/CN={cn}"])
  run(["openssl","ca","-batch","-config",str(CONFIG),"-extensions","client_cert","-days",str(days),"-in",str(csr),"-out",str(cert)])
  info=cert_info(cert);CERTS.mkdir(mode=0o750,parents=True,exist_ok=True);shutil.copyfile(cert,CERTS/f'{info["serial_number"]}.crt')
  password=secrets.token_urlsafe(24);token=secrets.token_urlsafe(32);package,metadata=package_paths(info["serial_number"]);PACKAGES.mkdir(mode=0o700,parents=True,exist_ok=True)
  env={**os.environ,"PKCS12_PASSWORD":password};run(["openssl","pkcs12","-export","-out",str(package),"-inkey",str(key),"-in",str(cert),"-certfile",str(CA_CERT),"-passout","env:PKCS12_PASSWORD"],env=env);package.chmod(0o600)
  expires=time.time()+600;metadata.write_text(json.dumps({"token_hash":hashlib.sha256(token.encode()).hexdigest(),"expires":expires}),encoding="utf-8");metadata.chmod(0o600)
  return {**info,"password":password,"download_token":token,"package_expires_at":datetime.fromtimestamp(expires,timezone.utc).isoformat()}
def download(payload):
 package,metadata=package_paths(payload.get("serial_number",""))
 if not package.exists() or not metadata.exists():raise RuntimeError("Установочный файл больше недоступен")
 data=json.loads(metadata.read_text());actual=hashlib.sha256(payload.get("download_token","").encode()).hexdigest()
 if time.time()>data["expires"] or not hmac.compare_digest(actual,data["token_hash"]):raise RuntimeError("Ссылка скачивания недействительна")
 content=base64.b64encode(package.read_bytes()).decode();package.unlink(missing_ok=True);metadata.unlink(missing_ok=True);return {"package":content}
def apply_crl():
 with tempfile.TemporaryDirectory(prefix="sales-crl-") as directory:
  candidate=Path(directory)/"crl.pem";run(["openssl","ca","-config",str(CONFIG),"-gencrl","-out",str(candidate)]);run(["openssl","crl","-in",str(candidate),"-noout"])
  backup=CRL.with_suffix(".pem.previous");had_old=CRL.exists()
  if had_old:shutil.copy2(CRL,backup)
  shutil.copy2(candidate,CRL)
  try:run(["/usr/bin/sudo","-n","/usr/local/sbin/sales-nginx-safe-reload"])
  except Exception:
   if had_old:os.replace(backup,CRL)
   else:CRL.unlink(missing_ok=True)
   raise
  backup.unlink(missing_ok=True)
def is_revoked(serial):
 normalized=re.sub(r"[^A-Fa-f0-9]","",serial).upper()
 if not normalized or not (ROOT/"index.txt").exists():return False
 for line in (ROOT/"index.txt").read_text(encoding="utf-8",errors="replace").splitlines():
  columns=line.split("\t")
  if len(columns)>3 and columns[0]=="R" and columns[3].lstrip("0").upper()==normalized.lstrip("0"):return True
 return False
def revoke(payload):
 serial=re.sub(r"[^A-Fa-f0-9]","",payload.get("serial_number",""));cert=CERTS/f"{serial}.crt"
 if not cert.exists():raise RuntimeError("Публичный сертификат для отзыва не найден")
 if not is_revoked(serial):
  try:run(["openssl","ca","-batch","-config",str(CONFIG),"-revoke",str(cert)])
  except RuntimeError:
   # OpenSSL мог успеть записать R в index.txt до ошибки следующего шага.
   if not is_revoked(serial):raise
 # CRL и reload повторяются даже для уже отозванного serial. Это позволяет
 # безопасно продолжить операцию после частичного сбоя предыдущего запроса.
 apply_crl();package,metadata=package_paths(serial);package.unlink(missing_ok=True);metadata.unlink(missing_ok=True);return {"revoked":True}
def register(payload):
 raw=base64.b64decode(payload.get("certificate",""),validate=True)
 with tempfile.NamedTemporaryFile(prefix="sales-existing-",delete=False) as target:target.write(raw);path=Path(target.name)
 pem=path.with_suffix(".pem")
 try:
  try:run(["openssl","x509","-in",str(path),"-out",str(pem)])
  except RuntimeError:run(["openssl","x509","-inform","DER","-in",str(path),"-out",str(pem)])
  run(["openssl","verify","-CAfile",str(CA_CERT),str(pem)]);info=cert_info(pem);CERTS.mkdir(mode=0o750,parents=True,exist_ok=True);shutil.copyfile(pem,CERTS/f'{info["serial_number"]}.crt');return info
 finally:path.unlink(missing_ok=True);pem.unlink(missing_ok=True)
OPERATIONS={"issue":issue,"download":download,"revoke":revoke,"register":register}
def cleanup_packages():
 while True:
  if PACKAGES.exists():
   for metadata in PACKAGES.glob("*.json"):
    try:
     if time.time()>json.loads(metadata.read_text()).get("expires",0):
      metadata.with_suffix(".p12").unlink(missing_ok=True);metadata.unlink(missing_ok=True)
    except Exception:metadata.unlink(missing_ok=True)
  time.sleep(60)
class Handler(socketserver.StreamRequestHandler):
 def handle(self):
  try:
   request=json.loads(self.rfile.readline(2*1024*1024));payload=request["payload"];raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode();expected=hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
   if not SECRET or not hmac.compare_digest(request.get("signature",""),expected):raise RuntimeError("Неверная подпись запроса")
   action=payload.get("action");operation=OPERATIONS.get(action)
   if not operation:raise RuntimeError("Операция не разрешена")
   response={"ok":True,"data":operation(payload)}
  except Exception as exc:response={"ok":False,"error":str(exc)[:1000]}
  self.wfile.write(json.dumps(response,ensure_ascii=False,separators=(",",":")).encode()+b"\n")
def main():
 if not SECRET or len(SECRET)<32:raise SystemExit("SALES_MTLS_HELPER_SECRET должен содержать не менее 32 символов")
 SOCKET.parent.mkdir(mode=0o750,parents=True,exist_ok=True);SOCKET.unlink(missing_ok=True)
 threading.Thread(target=cleanup_packages,daemon=True).start()
 with socketserver.UnixStreamServer(str(SOCKET),Handler) as server:SOCKET.chmod(0o660);server.serve_forever()
if __name__=="__main__":main()
