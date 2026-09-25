from pathlib import Path

SUPPORTED_SUFFIXES={".xls",".xlsx",".html",".htm"}

def importable_ftp_files(names):
 """Возвращает поддерживаемые файлы, включая EROOR_* для повторной обработки."""
 result=[]
 for value in names:
  name=Path(str(value).replace("\\","/")).name
  if name and name not in {".",".."} and Path(name).suffix.lower() in SUPPORTED_SUFFIXES:result.append(name)
 return list(dict.fromkeys(result))
