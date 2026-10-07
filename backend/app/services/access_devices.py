import re,secrets,unicodedata
from datetime import datetime,timezone

from app.models import AccessAuditEvent


def make_common_name(device_name:str)->str:
    latin=unicodedata.normalize("NFKD",device_name).encode("ascii","ignore").decode().lower()
    slug=re.sub(r"[^a-z0-9]+","-",latin).strip("-")[:35] or "device"
    return f"{slug}-{secrets.token_hex(3)}"


def effective_status(device,now:datetime|None=None)->str:
    now=now or datetime.now(timezone.utc)
    if device.status=="revoked":return "revoked"
    if device.expires_at<=now:return "expired"
    return device.status


def audit(db,device_id:int|None,action:str,ip_address:str|None,details:dict|None=None):
    db.add(AccessAuditEvent(device_id=device_id,action=action,actor="settings-admin",ip_address=ip_address,details=details or {}))
