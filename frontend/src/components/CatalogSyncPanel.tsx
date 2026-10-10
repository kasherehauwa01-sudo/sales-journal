import {useEffect, useRef, useState} from 'react';
import {api} from '../api/client';

type SyncState = {
  cycle: number; cursor: number; upper_id: number; processed: number;
  matched: number; not_found: number; ambiguous: number; invalid: number; errors: number;
  completed: boolean; started_at: string | null; last_success_at: string | null;
  last_full_success_at: string | null;
};
type Status = {
  worker_ready: boolean; status: string; requested: boolean; mode: 'discovery' | 'refresh';
  error_code: string | null; cycle_started_at: string | null; refresh_completed: boolean;
  scanned_this_run: number; processed_this_run: number; scan_rows_per_second: number | null;
  state: SyncState | null;
};
const names: Record<string, string> = {
  queued: 'Ожидает запуска', running: 'Работает', waiting: 'Ожидает восстановления ресурсов или API',
  stopping: 'Завершает текущий пакет', paused: 'Приостановлено', completed: 'Завершено', failed: 'Ошибка',
};
const stamp = (value: string | null | undefined) => value ? new Date(value).toLocaleString('ru-RU') : 'Нет данных';
const number = (value: number | undefined) => (value ?? 0).toLocaleString('ru-RU');

export function CatalogSyncPanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [mode, setMode] = useState<'discovery' | 'refresh'>('discovery');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sending = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  async function refresh(signal?: AbortSignal) {
    const current = ++generation.current;
    try {
      const result = await api<Status>('/settings/catalog-sync/status', {signal});
      if (mounted.current && current === generation.current && !signal?.aborted) {
        setStatus(result);
        setMode(result.mode);
      }
    } catch (exc) {
      if (mounted.current && !signal?.aborted) setError(exc instanceof Error ? exc.message : 'Не удалось получить статус');
    }
  }
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => { mounted.current = false; controller.abort(); };
  }, []);
  async function command(action: 'start' | 'pause') {
    if (sending.current) return;
    sending.current = true;
    setBusy(true); setError('');
    try {
      await api(`/settings/catalog-sync/${action}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        ...(action === 'start' ? {body: JSON.stringify({mode})} : {}),
      });
      await refresh();
    } catch (exc) {
      if (mounted.current) setError(exc instanceof Error ? exc.message : 'Не удалось выполнить команду');
    } finally {
      sending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  const state = status?.state;
  const active = !!status?.requested || ['queued', 'running', 'waiting', 'stopping'].includes(status?.status ?? '');
  return <section className="panel settings-panel catalog-sync-panel">
    <h2>Синхронизация каталога VRCatalog</h2>
    <p className="muted">Запускается только по вашей команде. После перезапуска worker нажмите «Продолжить».</p>
    {error && <div className="error" role="alert">{error}</div>}
    {status && <>
      <p aria-live="polite"><strong>Состояние: {names[status.status] ?? 'Неизвестно'}</strong></p>
      {!status.worker_ready && <p className="muted">Worker выключен или недоступен. Для запуска требуется включение отдельного сервиса администратором сервера.</p>}
      {status.error_code && <p role="status">Причина остановки или ожидания: <code>{status.error_code}</code>. Автоматических бесконечных повторов нет.</p>}
      <dl className="catalog-sync-metrics">
        <div><dt>Цикл</dt><dd>{number(state?.cycle)}</dd></div>
        <div><dt>Текущий курсор продаж</dt><dd>{number(state?.cursor)}</dd></div>
        <div><dt>Верхняя граница ID цикла</dt><dd>{number(state?.upper_id)}</dd></div>
        <div><dt>Проверено идентификаторов, накопительно</dt><dd>{number(state?.processed)}</dd></div>
        <div><dt>Сопоставлено</dt><dd>{number(state?.matched)}</dd></div>
        <div><dt>Не найдено</dt><dd>{number(state?.not_found)}</dd></div>
        <div><dt>Неоднозначно</dt><dd>{number(state?.ambiguous)}</dd></div>
        <div><dt>Некорректные идентификаторы</dt><dd>{number(state?.invalid)}</dd></div>
        <div><dt>Технические ошибки</dt><dd>{number(state?.errors)}</dd></div>
        <div><dt>Последняя успешная синхронизация</dt><dd>{stamp(state?.last_success_at)}</dd></div>
        <div><dt>Начало текущего цикла</dt><dd>{stamp(status.cycle_started_at)}</dd></div>
        <div><dt>Последний полный завершённый проход / сверка</dt><dd>{stamp(state?.last_full_success_at)}</dd></div>
        <div><dt>Прочитано строк за этот запуск</dt><dd>{number(status.scanned_this_run)}</dd></div>
        <div><dt>Скорость чтения</dt><dd>{status.scan_rows_per_second !== null ? `${number(status.scan_rows_per_second)} строк/с` : 'Нет достоверной оценки'}</dd></div>
      </dl>
      <p className="muted">Верхняя граница — ID, а не количество товаров или продаж. ID могут иметь пропуски, поэтому процент выполнения и оставшееся время не вычисляются. Счётчики включают повторные сверки.</p>
      {!status.cycle_started_at && <p className="muted">Время начала уже существовавшего цикла неизвестно; данные прежних запусков не подменяются оценкой.</p>}
    </>}
    <label>Режим
      <select aria-label="Режим синхронизации" value={mode} disabled={busy || active || (status?.refresh_completed === false)} onChange={event => setMode(event.target.value as typeof mode)}>
        <option value="discovery">Продолжить проход / обработать новые продажи</option>
        <option value="refresh" disabled={!state?.completed}>Сверить характеристики известных товаров</option>
      </select>
    </label>
    <div className="catalog-sync-actions">
      <button className="primary" disabled={busy || active || !status?.worker_ready} onClick={() => void command('start')}>{busy ? 'Отправка…' : 'Запустить / Продолжить'}</button>
      <button disabled={busy || !active || !status?.worker_ready} onClick={() => void command('pause')}>Приостановить</button>
      <button disabled={busy} onClick={() => {setError(''); void refresh();}}>Обновить статус</button>
    </div>
    <p className="muted">Остановка завершает текущий пакет и сохраняет прогресс. Сверка известных товаров выполняется отдельно, без повторного чтения всей истории продаж.</p>
  </section>;
}
