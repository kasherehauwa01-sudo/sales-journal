# Integration API Sales Journal → Calltrack

## 1. Назначение

API предоставляет Calltrack доступ **только на чтение** к продажам Sales Journal:

1. batch-получение кратких событий продаж нескольких клиентов для timeline;
2. ленивое получение полной карточки одной продажи с товарами после клика пользователя.

API не предоставляет методов создания, изменения или удаления продаж и не требует прямого доступа к PostgreSQL.

## 2. Base URL

Относительный префикс API:

```text
/api/integrations/calltrack
```

При стандартном production `BASE_PATH=/vr/sales` внешний URL имеет вид:

```text
https://<host>/vr/sales/api/integrations/calltrack
```

Домен не является частью контракта и должен задаваться в server-side конфигурации Calltrack.

## 3. Authentication

Все endpoints требуют server-to-server Bearer token:

```http
Authorization: Bearer <integration-token>
```

Sales Journal читает token из server-side ENV:

```dotenv
CALLTRACK_INTEGRATION_TOKEN=
```

Пустой, отсутствующий или неверный token приводит к `401 Unauthorized`. Token нельзя помещать во frontend, URL, Git или логи.

## 4. Идентификация клиента

В таблице продаж Sales Journal отсутствует стабильный `client_id` ClientsVR. Поэтому request использует:

* `key` — обязательный непрозрачный идентификатор клиента на стороне Calltrack; Sales Journal возвращает его как `client_key`;
* `phone` — предпочтительный фактический идентификатор продажи;
* `name` — резервное точное сопоставление.

Порядок сопоставления одной продажи:

1. нормализованный телефон;
2. точное нормализованное имя.

Fuzzy matching не используется. `ООО Ромашка` и `ООО Ромашка Плюс` — разные клиенты.

Телефон очищается от нецифровых символов. Для значения длиной не менее 10 цифр используются последние 10 цифр и префикс `+7`. Поэтому `+7 999 123-45-67`, `8 (999) 123-45-67` и `79991234567` сопоставляются как `+79991234567`.

Если одинаковый нормализованный телефон или одинаковое точное нормализованное имя переданы с разными `key`, API возвращает `422`, поскольку результат был бы неоднозначным.

## 5. Batch endpoint

```http
POST /api/integrations/calltrack/client-sales
Content-Type: application/json
Authorization: Bearer <integration-token>
```

### Request schema

```json
{
  "clients": [
    {
      "key": "string, 1..255, уникальный внутри batch",
      "phone": "string|null, максимум 64",
      "name": "string|null, максимум 512"
    }
  ],
  "date_from": "YYYY-MM-DD",
  "date_to": "YYYY-MM-DD"
}
```

Правила:

* от 1 до 500 клиентов;
* для каждого клиента обязателен `key` и хотя бы один из `phone`/`name`;
* `date_from <= date_to`;
* обе границы периода включительны;
* товары и `original_products_text` в batch response не загружаются;
* одна продажа возвращается один раз, дедупликация выполняется по `Sale.id`.

### Пример request

```json
{
  "clients": [
    {
      "key": "calltrack-client-42",
      "phone": "+7 999 123-45-67",
      "name": "ООО Ромашка"
    },
    {
      "key": "calltrack-client-84",
      "phone": null,
      "name": "Иванов И.И."
    }
  ],
  "date_from": "2026-09-01",
  "date_to": "2026-09-30"
}
```

### Response schema

```json
{
  "items": [
    {
      "sale_id": 123,
      "client_key": "calltrack-client-42",
      "matched_by": "phone",
      "sale_date": "2026-09-11",
      "document_number": "12345",
      "client": "ООО Ромашка",
      "phone": "+79991234567",
      "manager": "Иванов И.И.",
      "department": "Волгоград",
      "total_amount": "23450.00"
    }
  ]
}
```

### Поля Sale Summary

| Поле | Тип | Описание |
|---|---|---|
| `sale_id` | integer | Первичный ключ `sales.id`, используется detail endpoint |
| `client_key` | string | `key` соответствующего клиента из request |
| `matched_by` | `phone` или `name` | Фактически использованный способ сопоставления |
| `sale_date` | date | Дата продажи |
| `document_number` | string | Номер документа продажи |
| `client` | string/null | Имя клиента из Sales Journal |
| `phone` | string/null | Телефон, сохранённый у продажи |
| `manager` | string/null | Менеджер из ClientsVR; может быть `null`, если справочник недоступен |
| `department` | string | Подразделение/магазин |
| `total_amount` | decimal string | Итоговая сумма продажи |

Продажи сортируются по `sale_date`, затем по `sale_id`.

Если подходящих продаж нет или клиент неизвестен, это успешный ответ:

```http
HTTP/1.1 200 OK
```

```json
{"items": []}
```

## 6. Detail endpoint

```http
GET /api/integrations/calltrack/sales/{sale_id}
Authorization: Bearer <integration-token>
```

`sale_id` — значение `sales.id`, полученное в поле `sale_id` batch response.

Товары загружаются только при этом запросе.

### Пример detail response

```json
{
  "id": 123,
  "row_number": 10,
  "sale_date": "2026-09-11",
  "document_number": "12345",
  "client": "ООО Ромашка",
  "manager": "Иванов И.И.",
  "department": "Волгоград",
  "total_amount": "23450.00",
  "base_amount": "25000.00",
  "discount_percent": "6.20",
  "reason": null,
  "author": "Петров П.П.",
  "price_type": "Розница",
  "discount_card_percent": "5.00",
  "discount_card_number": "100500",
  "social": false,
  "certificate_amount": null,
  "promotion": null,
  "phone": "+79991234567",
  "original_products_text": "Исходное содержимое поля товаров",
  "created_at": "2026-09-11T12:00:00Z",
  "items": [
    {
      "id": 456,
      "article": "ART-1",
      "code": "001",
      "name": "Товар",
      "quantity": "2.000",
      "base_price": "1000.00",
      "actual_price": "900.00",
      "extra_data": null
    }
  ]
}
```

### Поля Sale Detail

`id`, `row_number`, `sale_date`, `document_number`, `client`, `manager`, `department`, `total_amount`, `base_amount`, `discount_percent`, `reason`, `author`, `price_type`, `discount_card_percent`, `discount_card_number`, `social`, `certificate_amount`, `promotion`, `phone`, `original_products_text`, `created_at`, `items`.

### Поля SaleItem

`id`, `article`, `code`, `name`, `quantity`, `base_price`, `actual_price`, `extra_data`.

Неизвестный `sale_id` возвращает `404`.

## 7. HTTP status codes

| Код | Значение |
|---|---|
| `200` | Успех, в том числе пустой список продаж |
| `401` | Отсутствующий, пустой или неверный Bearer token |
| `404` | Продажа для detail endpoint не найдена |
| `422` | Ошибка схемы, периода, лимита или неоднозначные идентификаторы |
| `500` | Внутренняя ошибка Sales Journal |

Недоступность Sales Journal определяется сетевой ошибкой или 5xx и не должна интерпретироваться как отсутствие продаж.

## 8. Примеры curl

Реальный secret в команды не подставляется. Он должен находиться в server-side ENV вызывающего сервиса.

```bash
curl -fsS \
  -X POST \
  -H "Authorization: Bearer $CALLTRACK_INTEGRATION_TOKEN" \
  -H "Content-Type: application/json" \
  "https://<host>/vr/sales/api/integrations/calltrack/client-sales" \
  --data '{
    "clients": [
      {"key": "calltrack-client-42", "phone": "+7 999 123-45-67", "name": "ООО Ромашка"}
    ],
    "date_from": "2026-09-01",
    "date_to": "2026-09-30"
  }'
```

```bash
curl -fsS \
  -H "Authorization: Bearer $CALLTRACK_INTEGRATION_TOKEN" \
  "https://<host>/vr/sales/api/integrations/calltrack/sales/123"
```

## 9. Производительность и N+1

Batch выполняет один SQL-запрос продаж для всего списка клиентов. Число SQL-запросов не растёт вместе с количеством клиентов. `SaleItem` не загружается для timeline. Отображение manager загружается одним кешируемым обращением к ClientsVR, а не запросом на каждого клиента.

Максимальный размер batch — 500 клиентов. Большие наборы вызывающая сторона должна разбивать на последовательные пакеты.
