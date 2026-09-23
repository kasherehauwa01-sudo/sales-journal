from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field
class ORMModel(BaseModel): model_config = ConfigDict(from_attributes=True)
class ItemOut(ORMModel):
    id:int; article:str|None; code:str|None; name:str; quantity:Decimal; base_price:Decimal|None; actual_price:Decimal|None; extra_data:str|None=None
class SaleOut(ORMModel):
    id:int; row_number:int|None; sale_date:date; document_number:str; client:str|None; manager:str|None=None; department:str; total_amount:Decimal; base_amount:Decimal|None; discount_percent:Decimal|None; reason:str|None; author:str|None; price_type:str|None; discount_card_percent:Decimal|None; discount_card_number:str|None; social:bool; certificate_amount:Decimal|None; promotion:str|None; phone:str|None; original_products_text:str|None; created_at:datetime; items:list[ItemOut]=Field(default_factory=list)
class SalePage(BaseModel): items:list[SaleOut]; total:int; page:int; page_size:int; pages:int
class ImportErrorOut(ORMModel): id:int; row_number:int|None; message:str
class ImportOut(ORMModel):
    id:int; filename:str; file_size:int; status:str; uploaded_at:datetime; period_start:date|None; period_end:date|None; total_rows:int; processed_rows:int; added_rows:int; duplicate_rows:int; skipped_rows:int; error_rows:int; duration_ms:int|None; error_text:str|None; log_text:str|None; errors:list[ImportErrorOut]=Field(default_factory=list)
