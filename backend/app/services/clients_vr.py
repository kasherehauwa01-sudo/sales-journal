import asyncio,json,time
from urllib.error import HTTPError
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen

cache:dict[str,tuple[float,list[str]]]={}
class ClientsVrError(RuntimeError):pass

def _request(paths:list[str]):
 from app.config import settings
 base=settings.clients_vr_api_url.rstrip("/");headers={"Accept":"application/json"}
 if settings.clients_vr_api_token:headers["Authorization"]=f"Bearer {settings.clients_vr_api_token}"
 last=None
 for path in paths:
  try:
   with urlopen(Request(f"{base}{path}",headers=headers),timeout=15) as response:return json.load(response)
  except HTTPError as exc:
   last=exc
   if exc.code not in {404,405,422}:raise
 raise last or RuntimeError("Не найден endpoint clients_vr")

def _source(payload,keys):
 if isinstance(payload,list):return payload
 if isinstance(payload,dict):
  source=next((payload[key] for key in (*keys,"items","data","results") if isinstance(payload.get(key),list)),None)
  if source is not None:return source
  nested=next((payload[key] for key in ("data","result") if isinstance(payload.get(key),dict)),None)
  if nested is not None:return _source(nested,keys)
 return []

def _items(payload,keys):
 source=_source(payload,keys);result=[]
 for item in source:
  value=item if isinstance(item,str) else next((item.get(key) for key in keys if item.get(key)),None) if isinstance(item,dict) else None
  if value and str(value).strip() not in result:result.append(str(value).strip())
 return sorted(result)

def _manager_clients(payload,manager):
 source=_source(payload,("clients",));filtered=[]
 for item in source:
  if isinstance(item,dict):
   owner=next((item.get(key) for key in ("manager","manager_name","manager_full_name","Менеджер") if item.get(key)),None)
   if owner and str(owner).strip().lower()!=manager.strip().lower():continue
  filtered.append(item)
 return _items(filtered,("client","name","client_name","full_name","Клиент"))

async def _cached(key,loader):
 saved=cache.get(key)
 if saved and time.monotonic()-saved[0]<300:return saved[1]
 try:values=await asyncio.to_thread(loader)
 except Exception as exc:raise ClientsVrError(f"clients_vr недоступен: {exc}") from exc
 cache[key]=(time.monotonic(),values);return values

async def get_managers():
 return await _cached("managers",lambda:_items(_request(["/managers","/clients/managers","/employees?role=manager","/clients"]),("manager","manager_name","manager_full_name","Менеджер","name","full_name")))

async def get_manager_clients(manager:str):
 encoded=quote(manager,safe="");params=urlencode({"manager":manager})
 return await _cached(f"clients:{manager}",lambda:_manager_clients(_request([f"/clients?{params}",f"/clients?manager_name={encoded}",f"/managers/{encoded}/clients",f"/clients"]),manager))

async def resolve_manager_filter(filters:dict,loader=None):
 """Заменяет прикладной фильтр менеджера на SQL-фильтр по его клиентам."""
 resolved=filters.copy();manager=resolved.pop("manager",None)
 if manager:resolved["clients"]=await (loader or get_manager_clients)(manager)
 return resolved
