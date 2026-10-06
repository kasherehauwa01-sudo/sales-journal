"""Безопасно создаёт scrypt-хеш пароля для SETTINGS_ADMIN_PASSWORD_HASH."""
from getpass import getpass

from app.services.settings_auth_crypto import hash_password


password = getpass("Новый пароль администратора настроек: ")
confirmation = getpass("Повторите пароль: ")
if password != confirmation:
    raise SystemExit("Пароли не совпадают")
if len(password) < 7:
    raise SystemExit("Пароль должен содержать не менее 7 символов")
print(hash_password(password))
