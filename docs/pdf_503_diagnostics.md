# PDF: различение HTTP503 и освобождение временного буфера

Дополнение: production-проба оператора установила падение Crashpad на launch.
Исправление окружения и проверка перед установкой: [pdf_crashpad_runtime.md](pdf_crashpad_runtime.md).

Это не подтверждение устранения конкретного production-инцидента. Скриншот с
«Сервер занят или недостаточно памяти» не позволяет отличить отказ повторного
запроса данных, admission/queue/memory, PDF floor512МиБ и ошибку Chromium.
На первом этапе production-журналы не были получены. Позже проба оператора
подтвердила launch/Crashpad failure; отдельная проверка временного окружения
ещё требуется (см. дополнение выше).

## Что исправлено

`reportResponse()` раньше заменял любой503 одной строкой, не читая причину.
Теперь фиксированные response headers несут безопасные машинные коды:

- X-Sales-Analytics-Error: memory_headroom / wait_timeout / queue_full / lock_unavailable.
- X-Sales-PDF-Error: memory_headroom / timeout / browser_unavailable / browser_failed.

UI использует закрытый словарь сообщений: неизвестные headers/response content
не отражаются в503сообщении. Распознаются также два известных сообщения старого
PDF backend; старое объединённое timeout/browser сообщение не объявляется
доказательством нехватки памяти. В кнопке PDF указывается этап:
подготовка данных, захват печатной версии или серверное формирование.
Повторные запросы автоматически не запускаются.

PDF renderer логирует только pdf_reason/pdf_stage и безопасные runtime metrics.
При PDF memory rejection указываются available_bytes и required_bytes. HTML,
фильтры, клиенты, URL изображений, токены и raw exception Chromium не логируются.
На этапах prepare/launch/context/layout/print видно место отказа.

В маршруте освобождаются последняя полученная chunk и bytearray JSON до рендера.
HTML передаётся во владение coroutine, Pydantic payload освобождается. После
подготовки документа исходный HTML больше не удерживается Python frame renderer;
после set_content освобождается и готовая строка документа. Данные PDF не урезаются.

Синтетический локальный замер16МиБ HTML, tracemalloc, живые Python allocations
при входе в renderer: до33589871байт, после16804620байт, разница16785251байт.
Это не пик RSS, не показатель RAM production и не обещание освобождения того же
объёма ОС при любом allocator. Отдельный тест weakref доказывает освобождение
буфера до рендера. JSON parsing всё ещё требует временной памяти; лимит32МиБ не повышен.

## Что сохранено

256МиБ общий minimum,512МиБ PDF minimum,10с admission wait, лимит очереди,
межпроцессный flock, nested single-flight, таймаут90с, cleanup Chromium, проверки
снимков и CORS/SSRF-защита фотографий. Статусы/основная структура ошибок прежние;
добавлены headers. Модели/БД/миграции/Compose/Docker/Swap не меняются.

## Чтение причины на текущем сервере — без обновления

После одной штатной неудачной попытки пользователя, только чтение:

```bash
docker logs --since 15m sales-journal-backend 2>&1 | \
  grep -E 'product_analytics runtime|POST /(api/)?reports/pdf|POST /(api/)?reports/product-sales/(summary|products)' | tail -n 60
awk '/MemAvailable:|SwapFree:/{print}' /proc/meminfo
```

В DevTools Network дополнительно проверить, какой запрос вернул503, его response
JSON и время. Это может быть summary/products/full во время prepare, а не /reports/pdf.
Не копировать request body, токены, содержимое отчёта или .env. Host MemAvailable
не заменяет cgroup headroom: runtime берёт минимум обоих, поэтому одной цифры free
недостаточно. Старый backend не логирует отдельный512МиБ rejection; его response
JSON всё же содержит конкретное сообщение, скрывавшееся интерфейсом.

Если нехватка памяти подтверждена, buffer fix может помочь при пограничном значении,
но постоянно недостаточный свободный запас требует отдельного ресурсного решения.
Не понижать floor/не отключать guards, не очищать volumes/images/другие проекты и
не запускать тяжёлый production-тест ради диагностики.

## Проверки и установка

Frontend146тестов, сборка tsc/Vite. Backend1034passed/113skipped (опциональные
интеграционные окружения/внешний updater); существующее AnyIO warning.
Тестируются body ownership,512МиБ отказ, browser launch/print/timeout, отсутствие
секретов в логах/ошибках, UI stages, ручной повтор без autoretry и прежняя runtime
сериализация. Реальные PDF на синтетических данных проверены Chromium.

После review нужны frontend и backend images, собранные вне VPS. Автоматически
ничего не устанавливать. Этот PR не заменяет фактическую диагностику текущего503;
до любых production-изменений необходимо установить его причину.
