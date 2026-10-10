import {StrictMode} from 'react';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {CatalogSyncPanel} from '../src/components/CatalogSyncPanel';

const paused = {
  worker_ready: true, status: 'paused', requested: false, mode: 'discovery',
  error_code: null, cycle_started_at: null, refresh_completed: true,
  scanned_this_run: 0, processed_this_run: 0, scan_rows_per_second: null,
  state: {cycle: 1, cursor: 77202, upper_id: 4623292, processed: 19062, matched: 18791,
    not_found: 265, ambiguous: 5, invalid: 1, errors: 0, completed: false,
    started_at: null, last_success_at: '2026-10-10T00:00:00Z', last_full_success_at: null},
};
let current: typeof paused;
let fetchMock: ReturnType<typeof vi.fn>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
beforeEach(() => {
  current = {...paused};
  fetchMock = vi.fn(async (_url, init?: RequestInit) => init?.method === 'POST' ? json({accepted: true}) : json(current));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});
const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

it('reads status in StrictMode without automatically starting synchronization', async () => {
  render(<StrictMode><CatalogSyncPanel/></StrictMode>);
  await screen.findByText('Состояние: Приостановлено');
  expect(posts()).toHaveLength(0);
  expect(screen.getByText(/ID могут иметь пропуски/)).toBeTruthy();
  expect(screen.getByText(/Время начала уже существовавшего цикла неизвестно/)).toBeTruthy();
});

it('sends one start command and disables repeat start while awaiting acknowledgement', async () => {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementation(async (_url, init?: RequestInit) => init?.method === 'POST' ? new Promise<Response>(r => {resolve = r;}) : json(current));
  render(<CatalogSyncPanel/>);
  await screen.findByText('Состояние: Приостановлено');
  const start = screen.getByRole('button', {name: 'Запустить / Продолжить'});
  fireEvent.click(start); fireEvent.click(start);
  expect(posts()).toHaveLength(1);
  current = {...paused, requested: true, status: 'running'};
  await act(async () => resolve(json({accepted: true})));
  await screen.findByText('Состояние: Работает');
  expect((screen.getByRole('button', {name: 'Запустить / Продолжить'}) as HTMLButtonElement).disabled).toBe(true);
});

it('pauses an active run and refreshes status explicitly', async () => {
  current = {...paused, requested: true, status: 'running'};
  render(<CatalogSyncPanel/>);
  await screen.findByText('Состояние: Работает');
  current = {...paused, requested: false, status: 'stopping'};
  fireEvent.click(screen.getByRole('button', {name: 'Приостановить'}));
  await screen.findByText('Состояние: Завершает текущий пакет');
  expect(posts()[0][0]).toContain('/pause');
  current = {...paused};
  fireEvent.click(screen.getByRole('button', {name: 'Обновить статус'}));
  await screen.findByText('Состояние: Приостановлено');
});

it('shows HTTP errors without automatic retries and allows manual retry', async () => {
  fetchMock.mockImplementation(async (_url, init?: RequestInit) => init?.method === 'POST' ? json({detail: 'Worker недоступен'}, 503) : json(current));
  render(<CatalogSyncPanel/>);
  await screen.findByText('Состояние: Приостановлено');
  fireEvent.click(screen.getByRole('button', {name: 'Запустить / Продолжить'}));
  await screen.findByRole('alert');
  expect(posts()).toHaveLength(1);
  expect(screen.getByText('Worker недоступен')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: 'Запустить / Продолжить'}));
  await waitFor(() => expect(posts()).toHaveLength(2));
});

it('disables start when the worker is offline and does not expose a fake progress bar', async () => {
  current = {...paused, worker_ready: false};
  render(<CatalogSyncPanel/>);
  await screen.findByText('Состояние: Приостановлено');
  expect((screen.getByRole('button', {name: 'Запустить / Продолжить'}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole('progressbar')).toBeNull();
});
