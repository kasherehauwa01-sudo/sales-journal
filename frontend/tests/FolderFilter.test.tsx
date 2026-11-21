import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { filterText } from '../src/services/reportPdf';
import { FolderFilter } from '../src/components/FolderFilter';
import { SalesDynamicsReportPage } from '../src/pages/SalesDynamicsReportPage';
vi.mock('recharts', () => {
  const Box = ({ children }: any) => <div>{children}</div>;
  return { ResponsiveContainer: Box, LineChart: Box, CartesianGrid: () => null, Legend: () => null,
    Line: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Picker() {
  const [value, setValue] = useState<string[]>([]);
  return <FolderFilter value={value} onChange={setValue} />;
}
it('searches descendants, keeps exact folder paths, supports multiple selections and clears them', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(['Посуда|Стекло|Бокалы', 'Посуда|Керамика', 'Текстиль']))));
  render(<Picker />);
  fireEvent.click(screen.getByRole('button', {name: /Все папки/}));
  fireEvent.click(await screen.findByRole('button', { name: 'Развернуть: Посуда' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Посуда' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Найти папку' }), { target: { value: 'бокалы' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Бокалы' }));
  expect(screen.getByRole('button', { name: /Выбрано папок: 2/ })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Убрать папку Посуда / Стекло / Бокалы' })).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: 'Найти папку' }), { target: { value: '' } });
  expect((screen.getByRole('checkbox', { name: 'Посуда' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Сбросить папки' }));
  expect(screen.getByRole('button', { name: /Все папки/ })).toBeTruthy();
});
it('shows a directory error rather than an empty successful tree', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({detail:'Недоступен'}), {status:502})));
  render(<Picker />);
  fireEvent.click(screen.getByRole('button', {name: /Все папки/}));
  expect((await screen.findByRole('alert')).textContent).toContain('Не удалось загрузить папки');
});
it('sends the folder selection only when building the report', async () => {
  const period = {start:'2026-09-01',end:'2026-09-30'};
  const comparison = {current:10,previous:5,difference:5,change_percent:100};
  const metrics = Object.fromEntries(['revenue','sales_count','items_count','average_check','average_items_per_sale'].map(key=>[key,comparison]));
  const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes('/sales-dynamics?') ? {period,requested_period:period,previous_period:period,metrics,chart:{group_by:'day',points:[]}} : url.includes('/catalog/folders') ? ['Посуда|Стекло'] : url.includes('/filters') ? {departments:[]} : [])));
  vi.stubGlobal('fetch', fetch);
  render(<SalesDynamicsReportPage />);
  fireEvent.click(screen.getByRole('button', {name: /Все папки/}));
  fireEvent.click(await screen.findByRole('checkbox', {name: 'Посуда'}));
  expect(fetch.mock.calls.some(([url]) => url.includes('/sales-dynamics?'))).toBe(false);
  fireEvent.click(screen.getByRole('button', {name: /Сформировать/}));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => new URL(url, 'http://test').searchParams.getAll('folders').includes('Посуда'))).toBe(true));
});

it('prints selected folder paths in PDF filter descriptions', () => {
  expect(filterText({folders:['Посуда|Стекло']})).toBe('Поиск по папке: Посуда / Стекло');
});
