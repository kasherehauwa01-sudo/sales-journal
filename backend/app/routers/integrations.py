from fastapi import APIRouter,HTTPException,Query
from app.services.clients_vr import ClientsVrError,get_manager_clients,get_managers

router=APIRouter(prefix="/integrations/clients-vr",tags=["Интеграции"])

@router.get("/managers")
async def managers():
 try:return await get_managers()
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc

@router.get("/clients")
async def clients(manager:str=Query(min_length=1,max_length=255)):
 try:return await get_manager_clients(manager)
 except ClientsVrError as exc:raise HTTPException(502,str(exc)) from exc
