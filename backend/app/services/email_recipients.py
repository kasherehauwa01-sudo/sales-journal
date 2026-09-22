import re

EMAIL=re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

def parse_recipient_emails(value:str)->list[str]:
 """Разбирает адреса из отдельных строк, удаляя пустые строки и дубликаты."""
 result=[]
 for line in value.splitlines():
  email=line.strip()
  if not email:continue
  if not EMAIL.fullmatch(email):raise ValueError(f"Некорректный email: {email}")
  if email.lower() not in {item.lower() for item in result}:result.append(email)
 if not result:raise ValueError("Укажите хотя бы один email получателя")
 return result
