import asyncio,json,time
from urllib.error import HTTPError
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen

MANAGER_ORDER=(
 "Пашута М.С.","Пашута М.С. (Ростов)","Пашута - сети","Родина","Родина Е.В. (Ростов)",
 "Селянкина Татьяна","Суркова Н.","Трошина Лариса","Шакулова Екатерина","Новожилова М.",
 "Королева Светлана","Ромащенко Екатерина","Антюфеева Яна","Бабушкина Виктория","Самойлова",
 "Андреева Дарья","Гаина Татьяна","Гордиенко","Ермохина Ирина","Кульченко Лилия","Никишова Ольга",
 "Пименова Любовь","Пирожкова Татьяна","Стародубцева Полина","Яицкая Ольга","СОТРУДНИК АВИАТОРОВ",
 "СОТРУДНИК АХТУБИНСК","СОТРУДНИК БАХТУРОВА","СОТРУДНИК ЕВРОПА","СОТРУДНИК ИДЕЯ",
 "СОТРУДНИК ПАРКХАУС","СОТРУДНИК ПРИВОЗ","СОТРУДНИК САНВЭЙ","СОТРУДНИК СТРОЙГРАД",
 "СОТРУДНИК ТУЛАК","СОТРУДНИК ЦИТРУС","СОТРУДНИК ЦУМ","Существующие сотрудники","Клишко Ю.Н.",
 "МАРКЕТПЛЕЙСЫ","Наш Китай","Нет менеджера","Дегтярев Алексей","Дегтярева Оксана Александровна",
 "!!!","<>","Временный","Иванчихина Елена","Клецкова Наталья","Салеев Александр Викторов",
 "СОТРУДНИК АРБУЗ",
)
cache:dict[str,tuple[float,object]]={}
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

def _client_managers(payload):
 result={}
 for item in _source(payload,("clients",)):
  if not isinstance(item,dict):continue
  client=next((item.get(key) for key in ("client","name","client_name","full_name","Клиент") if item.get(key)),None)
  manager=next((item.get(key) for key in ("manager","manager_name","manager_full_name","Менеджер") if item.get(key)),None)
  if client and manager:result[str(client).strip().lower()]=str(manager).strip()
 return result

async def _cached(key,loader):
 saved=cache.get(key)
 if saved and time.monotonic()-saved[0]<300:return saved[1]
 try:values=await asyncio.to_thread(loader)
 except Exception as exc:raise ClientsVrError(f"clients_vr недоступен: {exc}") from exc
 cache[key]=(time.monotonic(),values);return values

async def get_managers():
 managers=await _cached("managers",lambda:_items(_request(["/integration/managers","/managers","/clients/managers","/employees?role=manager","/integration/clients","/clients"]),("manager","manager_name","manager_full_name","Менеджер","name","full_name")))
 positions={name:index for index,name in enumerate(MANAGER_ORDER)}
 return sorted(managers,key=lambda name:(positions.get(name,len(positions)),name.lower()))

async def get_manager_clients(manager:str):
 encoded=quote(manager,safe="");params=urlencode({"manager":manager})
 return await _cached(f"clients:{manager}",lambda:_manager_clients(_request([f"/integration/clients?{params}",f"/integration/clients?manager_name={encoded}",f"/integration/managers/{encoded}/clients",f"/clients?{params}",f"/clients?manager_name={encoded}",f"/managers/{encoded}/clients",f"/integration/clients",f"/clients"]),manager))

async def get_client_manager(client:str|None):
 if not client:return None
 managers=await get_client_managers()
 return managers.get(client.strip().lower())

async def get_client_managers():
 return await _cached("client-managers",lambda:_client_managers(_request(["/integration/clients","/clients"])))

async def resolve_manager_filter(filters:dict,loader=None):
 """Заменяет прикладной фильтр менеджера на SQL-фильтр по его клиентам."""
 resolved=filters.copy();manager=resolved.pop("manager",None)
 if manager:resolved["clients"]=await (loader or get_manager_clients)(manager)
 return resolved
