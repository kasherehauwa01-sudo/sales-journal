"""Public diagnostics contain catalogued text only, never raw logs/errors/payloads."""
# code: title, component, explanation, remediation, severity
CATALOG = {
    'ALGORITHM_POLICY_NOT_APPROVED': ('Версия алгоритма не утверждена', 'Sales Journal', 'Утверждённая политика не соответствует текущему алгоритму.', 'Проверить /2, создать новую ревизию и явно утвердить её существующей CLI.', 'blocking'),
    'POLICY_NOT_APPROVED': ('Политика не утверждена', 'Sales Journal', 'Нет включённой утверждённой политики.', 'Проверить и утвердить политику через существующую административную процедуру.', 'blocking'),
    'IDENTITY_NOT_VERIFIED': ('Есть непроверенные привязки', 'Sales Journal', 'Предварительная или неоднозначная привязка не заменяет VERIFIED в этом producer.', 'Проверить ID, всю историю, уникальность и менеджера через identity-review; не подтверждать массово.', 'blocking'),
    'SOURCE_CHANGED_OR_PENDING': ('Источник изменён или идёт импорт', 'Sales Journal', 'Результат не соответствует текущей ревизии продаж или импорт не завершён.', 'Завершить импорт и штатно получить новый результат; не обходить защиту источника.', 'blocking'),
    'RUN_NOT_SUCCEEDED': ('Расчёт не завершён успешно', 'Sales Journal', 'Нет успешно завершённого расчёта.', 'Проверить worker и возобновить существующий процесс после устранения причины.', 'blocking'),
    'RUN_NOT_CURRENT_PUBLICATION': ('Расчёт не является текущим', 'Sales Journal', 'Выбранный результат не совпадает с текущей опубликованной генерацией.', 'Сверить сохранённое состояние worker и получить актуальный результат.', 'blocking'),
    'UPSTREAM_SIGNATURE_INVALID': ('Подтверждение источника не прошло проверку', 'Sales Journal', 'Нет допустимой независимой подписи источника.', 'Проверить утверждённый источник и подписанный manifest; не создавать подпись из локальной контрольной суммы.', 'blocking'),
    'PILOT_STOP': ('Пилот остановлен защитой', 'Внешний пилот', 'Внешний процесс сообщил об остановке.', 'Проверить подробную причину в защищённых журналах сервера.', 'error'),
    'REVIEWED_CODE_CHANGED': ('Изменён проверяемый код', 'Calltrack', 'SHA256 не соответствует утверждённому manifest.', 'Сравнить проверенную и текущую версии и обновить manifest только после явного аудита.', 'error'),
    'LIFETIME_BUDGET_EXHAUSTED': ('Общий лимит пилота исчерпан', 'Calltrack', 'Лимит на весь срок пилота достигнут.', 'Проверить счётчики и резервирования; изменение лимита требует отдельного утверждения. Не сбрасывать used_tasks.', 'blocking'),
    'DAILY_BUDGET_EXHAUSTED': ('Дневной лимит исчерпан', 'Calltrack', 'Лимит текущего московского дня достигнут.', 'Дождаться следующего дня либо согласовать изменение политики; не удалять резервирования.', 'blocking'),
    'BUDGET_UNCONFIRMED': ('Лимиты не подтверждены', 'Calltrack', 'Нет актуальных согласованных данных о лимитах или ledger не совпадает со счётчиком.', 'Проверить read-only сборщик, политику бюджета и ledger.', 'blocking'),
    'CALLTRACK_UNAVAILABLE': ('Calltrack недоступен', 'Calltrack', 'Нет подтверждённого чтения состояния получателя.', 'Восстановить связь и получить свежий статус; публикация не доказывает доставку.', 'error'),
    'AUTHORIZATION_FAILED': ('Ошибка авторизации', 'Интеграция', 'Не удалось подтвердить служебный доступ.', 'Проверить настройки доступа без вывода токенов и секретов.', 'error'),
    'NO_CURRENT_PUBLICATION': ('Нет актуальной разрешённой публикации', 'Sales Journal', 'Совместимая публикация текущего расчёта не найдена.', 'После проверки источников и привязок выполнить существующие preview/release с явным допуском.', 'blocking'),
    'DELIVERY_UNCONFIRMED': ('Получение Calltrack не подтверждено', 'Calltrack', 'Публикация есть, но нет связанного подтверждения приёма.', 'Сверить receipt по release_id и штатный consumer; не повторять создание задач вручную.', 'blocking'),
    'WORKER_FAILED': ('Ошибка worker', 'Sales Journal', 'Последняя попытка расчёта завершилась ошибкой.', 'Проверить worker и сохранённый checkpoint без сброса истории.', 'error'),
    'SYSTEMD_FAILED': ('Сервис завершился ошибкой', 'systemd', 'Последнее наблюдение содержит ошибку сервиса.', 'Проверить существующий calltrack-churn-pilot.service и причину отказа.', 'error'),
    'SOURCE_STALE': ('Данные или публикация устарели', 'Sales Journal', 'Дата расчёта/срок подтверждения не актуальны.', 'Получить актуальные источники и новый штатный расчёт.', 'blocking'),
    'RUNTIME_STATUS_MISSING': ('Нет статуса серверного процесса', 'Мониторинг', 'Root-owned снимок systemd/Calltrack не подключён.', 'Настроить ограниченный read-only сборщик; не давать backend доступ к systemd или Docker socket.', 'blocking'),
    'RUNTIME_STATUS_INVALID': ('Снимок статуса отклонён', 'Мониторинг', 'Неподдерживаемый формат, права или размер файла.', 'Проверить версию и защищённые права файла снимка.', 'blocking'),
    'RUNTIME_STATUS_STALE': ('Снимок статуса устарел', 'Мониторинг', 'Снимок старше 36 часов или имеет недопустимую дату.', 'Обновить read-only наблюдение существующего процесса.', 'blocking'),
    'SCHEDULE_UNCONFIRMED': ('Расписание не подтверждено', 'systemd', 'Не подтверждён активный существующий таймер на 09:00 Europe/Moscow.', 'Проверить существующий timer и его OnCalendar; не создавать второй таймер.', 'blocking'),
    'INTEGRITY_UNCONFIRMED': ('Целостность кода не подтверждена', 'Внешний пилот', 'Нет актуального результата проверки manifest.', 'Восстановить наблюдение за действующим manifest без отключения проверок.', 'blocking'),
    'RECEIVER_POLICY_UNCONFIRMED': ('Политика получателя не подтверждена', 'Calltrack', 'Нет утверждённого разрешения текущего generator в наблюдении Calltrack.', 'Отдельно проверить и утвердить новую версию на стороне получателя.', 'blocking'),
    'DB_STATUS_UNAVAILABLE': ('Не удалось прочитать состояние Sales Journal', 'Sales Journal', 'Read-only запрос статуса не выполнен.', 'Проверить соединение и совместимость схемы, не запускать миграцию из страницы.', 'error'),
    'RUNTIME_ERROR': ('Ошибка внешнего процесса', 'Внешний пилот', 'Получен неизвестный технический код; содержимое журнала не раскрывается.', 'Проверить защищённый журнал и версию сборщика.', 'error'),
}


CATALOG['RECEIVER_STATUS_MISSING'] = ('Нет наблюдения получателя', 'Мониторинг', 'Чтение состояния Calltrack не настроено; это не доказательство отказа самого Calltrack.', 'Подключить согласованный SELECT-only источник статуса получателя.', 'blocking')
CATALOG['RUNTIME_OBSERVATION_FAILED'] = ('Не удалось прочитать состояние сервера', 'Мониторинг', 'Сборщик не получил полное наблюдение systemd/journal.', 'Проверить права и фиксированные команды сборщика; не выдавать backend служебные привилегии.', 'blocking')
CATALOG['RECEIVER_SIGNALS_BLOCKED'] = ('Calltrack заблокировал часть сигналов', 'Calltrack', 'Есть подтверждённые отказы получателя для связанной публикации.', 'Проверить reasons_json соответствующих записей analytics_inbox через защищённый read-only доступ; не отправлять повторно вручную.', 'blocking')
CATALOG['MANAGER_BUDGET_EXHAUSTED'] = ('Лимит менеджера исчерпан', 'Calltrack', 'Достигнут отдельный lifetime cap по исходному менеджеру.', 'Проверить историю CREATED и согласованную политику менеджера; не удалять задачи/события для обхода cap.', 'blocking')
CATALOG['IDENTITY_CONFLICT'] = ('Дубли подтверждённых привязок', 'Sales Journal', 'Один клиент связан с несколькими подтверждёнными аналитическими субъектами.', 'Провести существующую проверку полной истории и конфликтов; не выбирать случайную привязку.', 'blocking')
for code, base, title in [
    ('PRODUCER_DISABLED', 'POLICY_NOT_APPROVED', 'Автоматизация producer выключена'),
    ('LOW_OR_UNAPPROVED_CONFIDENCE', 'POLICY_NOT_APPROVED', 'Недостаточная или неутверждённая уверенность'),
    ('CONFIDENCE_NOT_APPROVED', 'POLICY_NOT_APPROVED', 'Порог уверенности не утверждён'),
    ('REQUIRED_SOURCES_NOT_APPROVED', 'POLICY_NOT_APPROVED', 'Набор источников не утверждён'),
    ('MANIFEST_AGE_NOT_APPROVED', 'POLICY_NOT_APPROVED', 'Срок действия подтверждения не утверждён'),
    ('IDENTITY_OR_MANAGER_UNCONFIRMED', 'IDENTITY_NOT_VERIFIED', 'Клиент или менеджер не подтверждён'),
    ('CLIENT_ID_MULTIPLE_SUBJECTS_REQUIRES_HISTORY_MERGE', 'IDENTITY_CONFLICT', 'История содержит конфликтующие привязки'),
    ('ALGORITHM_CHANGED', 'ALGORITHM_POLICY_NOT_APPROVED', 'Результат рассчитан другим алгоритмом'),
    ('ALGORITHM_POLICY_CHANGED', 'ALGORITHM_POLICY_NOT_APPROVED', 'Checkpoint заменён после смены алгоритма'),
    ('NO_ELIGIBLE_VERIFIED_CHURN', 'IDENTITY_NOT_VERIFIED', 'Нет готовых подтверждённых кандидатов'),
    ('COMPLETENESS_OR_CLIENTS_UNCONFIRMED', 'SOURCE_CHANGED_OR_PENDING', 'Полнота источника или справочник клиентов не подтверждены'),
    ('UPSTREAM_PROOF_EXPIRED_OR_INVALID', 'UPSTREAM_SIGNATURE_INVALID', 'Подтверждение источника истекло или некорректно'),
    ('UPSTREAM_PROOF_MISMATCH', 'UPSTREAM_SIGNATURE_INVALID', 'Контекст подтверждения источника не совпадает'),
    ('UPSTREAM_SOURCE_SET_UNCONFIRMED', 'UPSTREAM_SIGNATURE_INVALID', 'Ревизии источников не подтверждены'),
    ('UPSTREAM_FACTS_MISMATCH', 'UPSTREAM_SIGNATURE_INVALID', 'Сохранённые факты не соответствуют подтверждению'),
    ('RUN_NOT_LAST_CLOSED_DAY', 'SOURCE_STALE', 'Расчёт не за последний закрытый день'),
    ('SCOPE_NOT_APPROVED', 'POLICY_NOT_APPROVED', 'Область расчёта не утверждена'),
    ('SHADOW_SOURCE_CONTRACT_INVALID', 'SOURCE_CHANGED_OR_PENDING', 'Контракт исходного расчёта несовместим'),
    ('AUTOMATION_TIMEZONE_UNSUPPORTED_OR_CHANGED', 'SCHEDULE_UNCONFIRMED', 'Часовой пояс автоматизации не соответствует политике'),
    ('IMMUTABLE_RELEASE_CONFLICT', 'NO_CURRENT_PUBLICATION', 'Конфликт неизменяемой публикации'),
]:
    _, component, explanation, remedy, severity = CATALOG[base]
    CATALOG[code] = (title, component, explanation, remedy, severity)


def diagnostic(code, detected_at=None):
    code = code if isinstance(code, str) and code in CATALOG else 'RUNTIME_ERROR'
    title, component, explanation, remediation, severity = CATALOG[code]
    return dict(code=code, title=title, component=component, explanation=explanation,
                recommendation=remediation, severity=severity, detected_at=detected_at,
                impact='Передача не подтверждена; статус не разрешает автоматизацию.')
