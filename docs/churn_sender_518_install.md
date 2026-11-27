# Постоянный отправитель к Calltrack PR #518

Контракт receiver зафиксирован на коммите
`00a8c9c85c7c03835187e152f5b7fd9a60714971`.
Этот пакет не устанавливает PHP receiver и не использует устаревшую копию
`deploy/churn-permanent/calltrack/` или `install-calltrack.sh`.
Receiver устанавливается отдельно по рассмотренному пакету Calltrack #518.

## Политики и доставка

Сохраняются разные pilot/permanent политики. В payload permanent:
`version=churn-permanent/1`, receiver `revision`, schedule `producer_revision`,
`daily_limit`, `cooldown_days`. Числа берутся из утверждённой schedule policy;
host не утверждает их и не заменяет своими defaults.
Calltrack сверяет точное совпадение и резервирует общий дневной лимит транзакционно.
Новая покупка после предыдущего эпизода и cooldown проверяются receiver.
Закрытие, удаление, повторное открытие и новый signal_id сами по себе не дают нового эпизода.

Пилотные 5/10 не являются входным разрешением или запретом permanent sender.
Receiver #518 сохраняет их историю и использует отдельный permanent ledger.
Действующие gates подписей, source trust, fresh bindings, менеджеров и ACL остаются.
Отключённая или неутверждённая schedule не возобновляет старую RELEASED job.

Host сверяет job/release/event_id/event_hash, полный набор receipts, решения и attribution.
BLOCKED/REVIEW — подтверждённые отказы, не TASK_CREATED.
Malformed/timeout/ошибка PHP/потеря ACK не считаются успехом: durable page остаётся
для повторной доставки с теми же event_json bytes.
Calltrack обеспечивает идемпотентность; backend ChurnReceipt не дублирует ACK.
Действующие bounded retries/backoff и предел четыре страницы за invocation сохранены.
Host failure записывается без содержимого доказательств/секретов.

## Что защищает manifest

`REQUIRED_FILES` покрывает статическую PHP include/require closure CLI receiver,
включая analytics_manager_pilot_budget.php, client_manager.php, email_sync.php,
personal_contacts_filter.php. CI проверяет closure по точному checkout #518.

Дополнительно обязательны выбранный provider/adapter, runner, observer, оба units,
php_environment JSON, локальная receiver policy и trusted_import JSON (если выбран).
Любой существующий /etc/calltrack/config.local.php, api/config.local.php или
корневой config.local.php также обязан присутствовать в manifest: это исполняемый PHP.
Появление такого файла после review блокирует запуск. Alias одного файла через
relative+absolute entries отклоняется. Полный manifest сверяется до работы и каждой страницы.

Manifest — заранее независимо утверждённый root-owned JSON:
`{"files": {"/absolute/path": "sha256", "api/file.php": "sha256"}}`.
Нет автоматического «пересчитать hashes и доверять». Смена любого обязательного
файла требует нового review manifest. Закрытые ключи и токены не печатаются.
Private config/key файлы должны быть regular, root-owned и не доступны группе/прочим.
Runtime manifest защищён от записи не-root пользователями.

## Состав host package

- backend/scripts/run_churn_automation.py → /usr/local/libexec/run_churn_automation.py
- backend/scripts/collect_churn_status.py → /usr/local/libexec/collect_churn_status.py
- TRUSTED_IMPORT: backend/scripts/churn_trusted_ftp.py → /usr/local/libexec/churn_trusted_ftp.py
- INDEPENDENT: отдельно рассмотренный executable provider → /usr/local/libexec/sales-churn-evidence
- deploy/churn-permanent/systemd/sales-churn-permanent.service → /etc/systemd/system/
- deploy/churn-permanent/systemd/sales-churn-permanent.timer → /etc/systemd/system/

`host-package.sha256.json` фиксирует bytes исходного пакета, включая installer.
Это контроль комплектности Git-пакета, не замена runtime approval.
Независимый provider здесь не реализуется: его signer/config/keys требуют отдельного review.

## Подготовка, без установки

Целевой host — Ubuntu 24.04, Docker, существующий sales-journal-backend.
Installer проверяет, что контейнер работает, но не заменяет image и не запускает команды в нём.
Версия backend должна отдельно подтверждать наличие app.churn_automation и
соответствующей уже установленной schema; installer не выполняет migrations.

Рабочий Calltrack root — /var/www/html/vr/calltrack-v3-20261026.
Поддерживаемый storage — /var/www/html/vr/calltrack-v3-20261026/storage.
Он явно разрешён в ProtectSystem=strict unit. Иной root/storage или drop-ins требуют
нового review; installer отказывает, а не расширяет filesystem sandbox.

До проверки оператор отдельно подготавливает:
1. Рассмотренный Git checkout и подтверждённый receiver #518.
2. Root-owned /etc/sales/churn-permanent/host.json:
   - calltrack_root;
   - manifest: /etc/sales/churn-permanent/runtime-code-sha256.json;
   - php_environment: абсолютный путь root:0600 JSON;
   - trusted_import: абсолютный путь root:0600 JSON, только при TRUSTED_IMPORT.
3. В php_environment: CALLTRACK_ANALYTICS_MODE="disabled",
   CALLTRACK_ANALYTICS_POLICY_FILE, обе непустые public keyrings
   CALLTRACK_UPSTREAM_PUBLIC_KEYS/CALLTRACK_BINDINGS_PUBLIC_KEYS (raw32 в base64),
   существующие DB settings и CALLTRACK_STORAGE_DIR при необходимости.
   Никаких ключей/паролей в Git, argv или shell history.
4. TRUSTED_IMPORT config: export_url (HTTPS analytics_verified_export.php),
   export_token_file, ed25519_key_file, key_id, hmac_key_file.
   Файлы root:0600; approval id/source mode должны совпасть на обеих сторонах.
   INDEPENDENT: отдельно рассмотренный provider с безопасным доступом к своим ключам.
5. Уже provisioned storage и /run/sales-churn-monitor с нужным reviewed group/mount
   для read-only мониторинга backend. Installer не создаёт и не меняет права этих данных.
6. Независимо утверждённый manifest, содержащий live PHP/config hashes и
   proposed host artifact hashes из данного Git-пакета (не старых установленных bytes).
7. Отдельно остановленные и отключённые old/new consumers/timers.
   Installer сам ничего не останавливает. Check требует inactive/failed, не enabled.

По умолчанию команда только проверяет:

```bash
python3 -B deploy/churn-permanent/install_host.py --source-mode TRUSTED_IMPORT
```

То же явно:

```bash
python3 -B deploy/churn-permanent/install_host.py --check --source-mode TRUSTED_IMPORT
```

Для INDEPENDENT вместо режима указать `--source-mode INDEPENDENT` и
`--evidence-provider /absolute/path/to/reviewed-provider`.
Check читает host/unit/container state, конфигурации и hashes; не вызывает
receiver PHP application, не подключается к MariaDB/PostgreSQL, не пишет файлы.
Печатает только planned destinations и hashes, не секреты.
Разрешение check не означает разрешение установки или включения.

## Установка — только отдельное действие оператора

После успешного review/check и отдельного разрешения установки:

```bash
python3 -B deploy/churn-permanent/install_host.py --apply --source-mode TRUSTED_IMPORT
```

Перед копированием повторяются проверки. В /var/backups/sales-churn-host-* сохраняются
оригинальные host files и restore.json (private directory).
Проверенные source bytes фиксируются в памяти и переносятся atomic replace;
при ошибке копирования уже изменённые файлы восстанавливаются.
Это не транзакция при power loss: private backup сохраняется для ручного восстановления.
Не перезапускать consumers, пока не проверены все installed hashes.

Installer НЕ выполняет daemon-reload, start, restart, enable, policy actions или migrations.
После установки оператор отдельно сверяет hashes и units (`systemd-analyze verify`),
проверяет backend image/schema compatibility, runtime manifests, sandbox и private keys.
Даже daemon-reload выполняется отдельно после review; он не должен сопровождаться enable/start.
Рассмотренный timer Persistent=false сам не включается.

## Откат и блокирующие условия

При выключенных consumers вернуть только host files из private backup по restore.json;
записи null означают отсутствовавший до установки файл. Не менять PHP, БД, policies,
ключи, receipts или consumed counters. Проверить соответствующий старый approved manifest.
Откат сам по себе не разрешает включать pilot или permanent.

До первого production цикла остаются отдельные условия:
- merge/review обоих Draft PR и сверка actual PHP/config/runtime hashes;
- отдельно утверждённые permanent limits/cooldown/revisions и source trust;
- подтверждение backend image и существующей schema без автоматической миграции;
- действительные signers, export permissions, runtime storage/mount/group/sandbox;
- полный PostgreSQL → host → Calltrack/MariaDB E2E на изолированном стенде;
- отдельное разрешение включения automation.

CI использует doubles/SQLite и одноразовую MariaDB с настоящим signed PHP receiver.
Это не проверка настоящего FirstVDS или клиентских данных.
