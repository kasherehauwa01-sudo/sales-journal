import asyncio,json,time
from urllib.error import HTTPError
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen
from fastapi import APIRouter,HTTPException,Query
from app.config import settings

router=APIRouter(prefix="/integrations/clients-vr",tags=["Интеграции"])
cache:dict[str,tuple[float,list[str]]]={}

def _request(paths:list[str]):
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
 source=_source(payload,keys)
 result=[]
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
 except Exception as exc:raise HTTPException(502,f"clients_vr недоступен: {exc}")
 cache[key]=(time.monotonic(),values);return values

@router.get("/managers")
async def managers():
 return await _cached("managers",lambda:_items(_request(["/managers","/clients/managers","/employees?role=manager","/clients"]),("manager","manager_name","manager_full_name","Менеджер","name","full_name")))

@router.get("/clients")
async def clients(manager:str=Query(min_length=1,max_length=255)):
 encoded=quote(manager,safe="");params=urlencode({"manager":manager})
 return await _cached(f"clients:{manager}",lambda:_manager_clients(_request([f"/clients?{params}",f"/clients?manager_name={encoded}",f"/managers/{encoded}/clients",f"/clients"]),manager))
