// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { CategorySeasonalityPage } from "../src/pages/CategorySeasonalityPage";
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  LineChart: ({ children }: any) => <div>{children}</div>,
  Line: () => null,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
}));
const profile = (peak: number, trough: number) => ({
  cells: Array.from({ length: 12 }, (_, i) => ({
    index: i === 1 ? null : i === peak ? 50 : i === trough ? -30 : 0,
    stores: i === 1 ? [] : ["Store"],
    years: [2024, 2025, 2026],
    estimated: true,
    reason: i === 1 ? "Недостаточная регулярность" : null,
    notes: [],
  })),
  peaks: [peak + 1],
  troughs: [trough + 1],
  estimated: true,
  actual: [
    {
      store: "Store",
      year: 2025,
      month: peak + 1,
      value: 50,
      method: "estimated",
      included: true,
      reason: null,
    },
  ],
});
export const rows = [
  {
    key: '["category:1","Чашки"]',
    parent_key: "category:1",
    parent: "Посуда",
    name: "Чашки",
    units: profile(3, 0),
    revenue: profile(3, 0),
  },
  {
    key: '["category:1","Тарелки"]',
    parent_key: "category:1",
    parent: "Посуда",
    name: "Тарелки",
    units: profile(6, 2),
    revenue: profile(6, 2),
  },
];
const options = {
  years: [2026, 2025, 2024],
  stores: ["Store"],
  nodes: [
    {
      id: "category:1",
      name: "Посуда",
      children: rows.map((r) => ({
        id: r.key,
        value: r.key,
        name: r.name,
        children: [],
      })),
    },
  ],
  brand: ["A", "B"],
  manufacturer: ["X", "Y"],
  managers: ["M"],
  buyer_types: ["Опт"],
};
let calls: any[], fail: boolean, missing: boolean;
const reply = (v: any, status = 200) =>
  new Response(JSON.stringify(v), {
    status,
    headers: { "Content-Type": "application/json" },
  });
function result(p: any) {
  return {
    state: missing ? "uninitialized" : "ready",
    rows: missing
      ? []
      : rows.filter(
          (r) =>
            !p.categories.length ||
            p.categories.includes("category:1") ||
            p.categories.includes(r.key),
        ),
    options: missing ? {} : options,
    filters: p,
    snapshot_id: missing ? null : "snapshot-1",
    last_success: missing ? null : "2026-11-13T12:00:00Z",
    through: "2026-10-31",
    next_update: "2026-11-14T16:00:00Z",
    updating: false,
    previous_after_error: false,
  };
}
beforeEach(() => {
  calls = [];
  fail = false;
  missing = false;
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const p = JSON.parse(init.body as string);
      calls.push({ url, p });
      return fail ? reply({ detail: "failure" }, 503) : reply(result(p));
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("automatically reads ready snapshot, all history default, no build button, metric and detail use snapshot", async () => {
  render(<CategorySeasonalityPage />);
  await screen.findByText("Тепловая карта спроса");
  expect(calls).toHaveLength(1);
  expect(calls[0].p.years).toEqual([]);
  expect(screen.queryByText("Сформировать отчёт")).toBeNull();
  expect(screen.queryByText(/2026 к 2025/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Выручка", exact: true }));
  expect(calls).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Чашки", exact: true }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].url).toContain("/detail");
  expect(calls[1].p.snapshot_id).toBe("snapshot-1");
  expect(screen.getByRole("dialog").textContent).toContain("Чашки");
  expect(screen.getByRole("dialog").textContent).not.toContain("Тарелки");
  expect(screen.getAllByText("Пик спроса").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Максимальный спад").length).toBeGreaterThan(0);
});
test("filters update automatically and 503 retries are manual quick reads", async () => {
  render(<CategorySeasonalityPage />);
  await screen.findByText("Тепловая карта спроса");
  fireEvent.click(screen.getByRole("button", { name: "Магазин", exact: true }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Store" }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].p.stores).toEqual(["Store"]);
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Бренд", exact: true }));
  fireEvent.click(screen.getByRole("checkbox", { name: "A", exact: true }));
  await screen.findByText(/Ошибка быстрого запроса/);
  expect(calls).toHaveLength(3);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
  await waitFor(() => expect(calls).toHaveLength(4));
  expect(
    calls.every((c) => c.url.includes("/reports/category-seasonality")),
  ).toBe(true);
});
test("uninitialized never launches background calculation", async () => {
  missing = true;
  render(<CategorySeasonalityPage />);
  await screen.findByText("Отчёт ещё не рассчитан");
  expect(calls).toHaveLength(1);
  expect(
    screen.getByText(/Первое заполнение выполняет администратор/),
  ).toBeTruthy();
});
test("late response cannot overwrite newer filter result", async () => {
  let release: ((v: Response) => void) | undefined;
  const fn = vi.fn(async (url: string, init: RequestInit) => {
    const p = JSON.parse(init.body as string);
    calls.push({ url, p });
    if (calls.length === 2) return new Promise<Response>((r) => (release = r));
    return reply(result(p));
  });
  vi.stubGlobal("fetch", fn);
  render(<CategorySeasonalityPage />);
  await screen.findByText("Тепловая карта спроса");
  fireEvent.click(screen.getByRole("button", { name: "Магазин", exact: true }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Store" }));
  await waitFor(() => expect(calls).toHaveLength(2));
  fireEvent.click(screen.getByRole("checkbox", { name: "Store" }));
  await waitFor(() => expect(calls).toHaveLength(3));
  release!(
    reply({
      ...result(calls[1].p),
      rows: [{ ...rows[0], name: "УСТАРЕВШИЙ" }],
    }),
  );
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByText("УСТАРЕВШИЙ")).toBeNull();
});
