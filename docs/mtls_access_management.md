# Управление mTLS-доступами Sales Journal

## Граница безопасности

Backend не получает `/etc/nginx`, `ca.key`, Docker socket или root-доступ. Он
обращается к host-side helper через `/run/sales-mtls-helper/helper.sock`.
Helper допускает только `issue`, `register`, `download` и `revoke`; shell-команды
и пути от API не принимаются.

## Подготовка CA state

Перед работами сделайте резервную копию `/etc/nginx/sales-mtls`. Проверьте, что
существуют `index.txt`, `serial`, `crlnumber`, `newcerts` и `issued`. Если CA ранее
использовался без `openssl ca`, состояние нужно подготовить вручную, не меняя
`ca.crt`/`ca.key`. Скопируйте `deploy/mtls-helper/openssl.cnf` в каталог CA.

## Установка helper

1. Создайте системного пользователя `sales-mtls` и группу `sales-backend`.
2. Дайте пользователю helper минимальные права на CA state и каталог `issued`.
3. Установите `sales-nginx-safe-reload` как `root:root` с режимом `0755`.
4. Установите файл `sudoers` через `visudo -cf`.
5. Создайте `/etc/sales-mtls-helper.env` с правами `0600`:

   ```dotenv
   SALES_MTLS_HELPER_SECRET=<openssl rand -hex 32>
   ```

6. Установите systemd unit, выполните `systemctl daemon-reload` и запустите helper.
7. То же значение задайте backend как `MTLS_HELPER_SECRET`.

## Nginx

В существующий HTTPS `server` Sales Journal добавьте:

```nginx
ssl_crl /etc/nginx/sales-mtls/crl.pem;
```

В API location передайте `X-Sales-MTLS-Verify`, `X-Sales-MTLS-CN`,
`X-Sales-MTLS-Serial` и секретный `X-Sales-Proxy-Secret`. Значение последнего
должно совпадать с `MTLS_PROXY_SECRET` и не должно коммититься. Создайте
root-only файл `/etc/nginx/snippets/sales-mtls-proxy-secret.conf`:

```nginx
proxy_set_header X-Sales-Proxy-Secret "<случайный секрет не короче 32 символов>";
```

Всегда выполняйте `nginx -t` перед первым reload. Helper при отзыве использует
фиксированный root-wrapper, который также сначала выполняет `nginx -t` и только
после успеха reload. При ошибке helper восстанавливает предыдущий CRL.

## Миграция и запуск

```bash
docker compose run --rm backend alembic upgrade head
docker compose up -d --build backend frontend
```

## Регистрация inessa-pc-01

Откройте «Настройки → Доступы» после входа администратором и используйте процедуру
регистрации существующего публичного сертификата. Загружается только `.crt`/PEM;
приватный ключ не нужен. Helper проверяет подпись через действующий `ca.crt`.

Production CA, CRL и Nginx не изменяются автоматически при развертывании кода.
