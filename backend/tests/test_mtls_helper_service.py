import importlib.util
import json
import subprocess
from pathlib import Path


def load_helper():
    path=Path(__file__).parents[2]/"deploy/mtls-helper/service.py"
    spec=importlib.util.spec_from_file_location("mtls_helper_service",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def command(*args):subprocess.run(args,check=True,capture_output=True,text=True)


def test_issue_download_and_revoke_with_temporary_ca(tmp_path,monkeypatch):
    helper=load_helper();root=tmp_path/"ca";root.mkdir();(root/"newcerts").mkdir();(root/"issued").mkdir();(root/"index.txt").write_text("");(root/"serial").write_text("1000\n");(root/"crlnumber").write_text("1000\n")
    command("openssl","req","-x509","-newkey","rsa:2048","-nodes","-keyout",str(root/"ca.key"),"-out",str(root/"ca.crt"),"-days","30","-subj","/CN=Test CA")
    template=(Path(__file__).parents[2]/"deploy/mtls-helper/openssl.cnf").read_text().replace("/etc/nginx/sales-mtls",str(root));(root/"openssl.cnf").write_text(template)
    monkeypatch.setattr(helper,"ROOT",root);monkeypatch.setattr(helper,"CONFIG",root/"openssl.cnf");monkeypatch.setattr(helper,"CA_CERT",root/"ca.crt");monkeypatch.setattr(helper,"CA_KEY",root/"ca.key");monkeypatch.setattr(helper,"CERTS",root/"issued");monkeypatch.setattr(helper,"PACKAGES",tmp_path/"packages")

    result=helper.issue({"common_name":"accounting-pc-01","validity_days":30})
    cert=root/"issued"/f'{result["serial_number"]}.crt';text=subprocess.run(["openssl","x509","-in",str(cert),"-text","-noout"],check=True,capture_output=True,text=True).stdout
    assert "TLS Web Client Authentication" in text
    assert "CA:FALSE" in text
    assert result["password"] not in json.dumps((root/"index.txt").read_text())

    package=helper.download({"serial_number":result["serial_number"],"download_token":result["download_token"]})
    assert package["package"]
    try:helper.download({"serial_number":result["serial_number"],"download_token":result["download_token"]})
    except RuntimeError as exc:assert "недоступен" in str(exc)
    else:raise AssertionError("Повторное скачивание должно быть запрещено")

    monkeypatch.setattr(helper,"apply_crl",lambda:None)
    assert helper.revoke({"serial_number":result["serial_number"]})=={"revoked":True}
