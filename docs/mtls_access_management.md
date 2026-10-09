# Управление mTLS-доступами Sales Journal

## Граница безопасности

Backend не получает `/etc/nginx`, `ca.key`, Docker socket или root-доступ. Он
обращается к host-side helper через `/run/sales-mtls-helper/helper.sock`.
Helper допускает только `issue`, `register`, `download` и `revoke`; команды и
пути от API не принимаются. Запросы дополнительно подписаны HMAC.

## 1. Пользователь, группа и GID контейнера

Создайте отдельную host-группу и непривилегированного пользователя:

```bash
getent group sales-backend >/dev/null || sudo groupadd --system sales-backend
id sales-mtls >/dev/null 2>&1 || sudo useradd --system --no-create-home \
  --shell /usr/sbin/nologin --gid sales-backend sales-mtls
getent group sales-backend
```

Запишите числовой GID из третьего поля в production `.env`. Не копируйте GID с
другого сервера — он может отличаться:

```bash
SALES_BACKEND_GID="$(getent group sales-backend | cut -d: -f3)"
printf 'SALES_BACKEND_GID=%s\n' "$SALES_BACKEND_GID"
```

Compose добавляет этот GID процессу backend через `group_add`, поэтому контейнер
может подключиться к socket `sales-mtls:sales-backend 0660`. После изменения GID
backend обязательно пересоздаётся, обычного restart недостаточно:

```bash
docker compose up -d --force-recreate backend
```

## 2. Подготовка CA state и прав

Сначала сделайте резервную копию `/etc/nginx/sales-mtls`. Не заменяйте
существующие `ca.crt` и `ca.key`. OpenSSL создаёт `serial.new` и `index.txt.new`
в корне CA, поэтому write-доступ нужен не только для `newcerts`:

```bash
sudo install -d -o root -g sales-backend -m 0770 /etc/nginx/sales-mtls
sudo install -d -o sales-mtls -g sales-backend -m 0750 \
  /etc/nginx/sales-mtls/newcerts /etc/nginx/sales-mtls/issued

sudo touch /etc/nginx/sales-mtls/index.txt
sudo test -s /etc/nginx/sales-mtls/serial || echo 1000 | sudo tee /etc/nginx/sales-mtls/serial >/dev/null
sudo test -s /etc/nginx/sales-mtls/crlnumber || echo 1000 | sudo tee /etc/nginx/sales-mtls/crlnumber >/dev/null
sudo chown sales-mtls:sales-backend \
  /etc/nginx/sales-mtls/index.txt /etc/nginx/sales-mtls/serial \
  /etc/nginx/sales-mtls/crlnumber
sudo chmod 0640 /etc/nginx/sales-mtls/index.txt \
  /etc/nginx/sales-mtls/serial /etc/nginx/sales-mtls/crlnumber
sudo chown root:sales-backend /etc/nginx/sales-mtls/ca.key
sudo chmod 0640 /etc/nginx/sales-mtls/ca.key
sudo install -o root -g sales-backend -m 0640 deploy/mtls-helper/openssl.cnf \
  /etc/nginx/sales-mtls/openssl.cnf
```

Если `crl.pem` уже существует, оставьте его на месте и обеспечьте helper право
замены файла внутри каталога. Backend этот каталог не монтирует.

## 3. Установка helper

```bash
sudo install -o root -g root -m 0755 deploy/mtls-helper/sales-nginx-safe-reload \
  /usr/local/sbin/sales-nginx-safe-reload
sudo install -o root -g root -m 0644 deploy/mtls-helper/sales-mtls-helper.service \
  /etc/systemd/system/sales-mtls-helper.service
sudo install -o root -g root -m 0440 deploy/mtls-helper/sudoers \
  /etc/sudoers.d/sales-mtls-helper
sudo visudo -cf /etc/sudoers.d/sales-mtls-helper
```

Создайте `/etc/sales-mtls-helper.env` с режимом `0600`:

```dotenv
SALES_MTLS_HELPER_SECRET=<openssl rand -hex 32>
```

То же значение укажите как `MTLS_HELPER_SECRET` backend. Затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sales-mtls-helper
sudo systemctl status --no-pager sales-mtls-helper
sudo stat -c '%U:%G %a %n' /run/sales-mtls-helper/helper.sock
```

Ожидаемые владелец и режим socket: `sales-mtls:sales-backend 660`.
`ProtectSystem=strict` остаётся включён. Helper имеет write-доступ только к CA
state, своему runtime/state, `/run/nginx.pid` и `/var/log/nginx`, необходимым для
проверенного reload Nginx.

Unit использует `RuntimeDirectoryPreserve=yes`: при `systemctl restart` каталог
не меняет inode, и bind mount backend продолжает видеть новый socket. После
первого обновления старого unit либо если каталог всё же был удалён вручную,
пересоздайте backend и проверьте socket из контейнера:

```bash
sudo systemctl restart sales-mtls-helper
docker compose up -d --force-recreate backend
docker compose exec backend python -c \
  'import os,socket; p="/run/sales-mtls-helper/helper.sock"; print(os.stat(p)); s=socket.socket(socket.AF_UNIX); s.connect(p); s.close()'
```

## 4. Nginx и CRL

В существующий HTTPS `server` Sales Journal добавьте:

```nginx
ssl_crl /etc/nginx/sales-mtls/crl.pem;
```

Передайте в API location заголовки из `deploy/nginx-location.conf`. Создайте
root-only `/etc/nginx/snippets/sales-mtls-proxy-secret.conf`; секрет должен
совпадать с `MTLS_PROXY_SECRET` backend и не должен попадать в Git:

```nginx
proxy_set_header X-Sales-Proxy-Secret "<случайный секрет не короче 32 символов>";
```

```bash
sudo nginx -t
sudo systemctl reload nginx
```

При отзыве helper проверяет `index.txt`. Если serial уже имеет статус `R`, он не
повторяет `openssl ca -revoke`, но заново генерирует CRL, выполняет `nginx -t` и
reload. Поэтому после сбоя reload кнопку «Заблокировать» можно безопасно нажать
повторно. Статус в БД меняется только после успешного применения CRL.

Безопасная проверка после тестового отзыва:

```bash
sudo openssl crl -in /etc/nginx/sales-mtls/crl.pem -noout -text
sudo nginx -t
sudo journalctl -u sales-mtls-helper -n 100 --no-pager
```

Проверьте, что отозванное устройство получает 403, а другое активное устройство
по-прежнему открывает Sales Journal.

## 5. Миграция и обновление приложения

```bash
docker compose config >/dev/null
docker compose run --rm backend alembic upgrade head
docker compose up -d --build --force-recreate backend frontend
```

## 6. Регистрация `inessa-pc-01`

Откройте «Настройки → Доступы» после входа администратором и зарегистрируйте
существующий публичный `.crt`/PEM. Приватный ключ не требуется. Helper проверяет
сертификат через действующий `ca.crt` и не перевыпускает его.

Production CA, CRL и Nginx не изменяются автоматически при развертывании кода.
