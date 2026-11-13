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
  Legend: () => null,
}));
const p = (month: number) => ({
  indices: Array.from({ length: 12 }, (_, i) => (i === month ? 100 : -10)),
  years: [2023, 2024, 2025],
  annual: [2023, 2024, 2025].map((year) => ({
    year,
    values: Array.from({ length: 12 }, (_, i) => (i === month ? 50 : 10)),
    indices: Array.from({ length: 12 }, (_, i) => (i === month ? 100 : -10)),
    included: true,
    reason: null,
    coverage: Array(12).fill(true),
  })),
  peaks: [{ month: month + 1, count: 3, total: 3, repeating: true }],
  message: null,
});
const rows = [
  {
    key: "a",
    parent_key: "parent",
    parent: "Посуда",
    name: "Чашки",
    units: p(3),
    revenue: p(3),
  },
  {
    key: "b",
    parent_key: "parent",
    parent: "Посуда",
    name: "Тарелки",
    units: p(6),
    revenue: p(6),
  },
];
let calls: any[],
  fail: boolean,
  release: ((v: Response) => void) | undefined,
  hold: boolean;
const reply = (body: any, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
beforeEach(() => {
  calls = [];
  fail = false;
  hold = false;
  release = undefined;
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (
        url.endsWith("/reports/category-seasonality") &&
        init?.method === "POST"
      ) {
        const data = JSON.parse(init.body as string);
        calls.push(data);
        if (hold)
          return new Promise<Response>((r) => {
            release = r;
          });
        if (fail) return reply({ detail: "busy" }, 503);
        return reply({
          rows,
          years: data.years,
          filters: data,
          coverage: {},
          warning: "Покрытие проверено",
        });
      }
      if (url.includes("/category-seasonality/options"))
        return reply({
          years: [2025, 2024, 2023],
          brand: ["Бренд"],
          manufacturer: ["Завод"],
          nodes: [
            {
              id: "parent",
              name: "Посуда",
              children: rows.map((r) => ({
                id: r.key,
                value: r.key,
                name: r.name,
                children: [],
              })),
            },
          ],
        });
      if (url.endsWith("/filters")) return reply({ departments: ["A"] });
      return reply([]);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("does not calculate on mount or editing filters; one click and metric/detail reuse the snapshot", async () => {
  render(<CategorySeasonalityPage />);
  await screen.findByText("Сформируйте отчёт");
  await waitFor(() =>
    expect(screen.queryByText("Загрузка справочников…")).toBeNull(),
  );
  expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Магазин", exact: true }));
  fireEvent.click(screen.getByText("A"));
  expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Сформировать отчёт" }));
  await screen.findByText("Тепловая карта спроса");
  expect(calls).toHaveLength(1);
  expect(calls[0].stores).toEqual(["A"]);
  fireEvent.click(screen.getByRole("button", { name: "Выручка" }));
  expect(calls).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Чашки", exact: true }));
  expect(screen.getByRole("dialog").textContent).toContain("Чашки");
  expect(screen.getByRole("dialog").textContent).toContain("Посуда");
  expect(screen.getByRole("dialog").textContent).not.toContain("Тарелки");
  expect(calls).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  fireEvent.click(screen.getByRole("button", { name: "Тарелки", exact: true }));
  expect(screen.getByRole("dialog").textContent).toContain("Тарелки");
});
test("503 has no automatic retry, manual retry succeeds", async () => {
  fail = true;
  render(<CategorySeasonalityPage />);
  await waitFor(() =>
    expect(screen.queryByText("Загрузка справочников…")).toBeNull(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сформировать отчёт" }));
  await screen.findByText(/Сервер занят/);
  expect(calls).toHaveLength(1);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
  await screen.findByText("Тепловая карта спроса");
  expect(calls).toHaveLength(2);
});
test("double click while pending sends one request", async () => {
  hold = true;
  render(<CategorySeasonalityPage />);
  await waitFor(() =>
    expect(screen.queryByText("Загрузка справочников…")).toBeNull(),
  );
  const b = screen.getByRole("button", { name: "Сформировать отчёт" });
  fireEvent.click(b);
  fireEvent.click(b);
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(b.hasAttribute("disabled")).toBe(true);
  release!(
    reply({
      rows,
      years: [2025, 2024, 2023],
      filters: calls[0],
      warning: "Покрытие",
    }),
  );
  await screen.findByText("Тепловая карта спроса");
});

test("editing after generation preserves snapshot and does not recalculate", async () => {
  render(<CategorySeasonalityPage />);
  await waitFor(() =>
    expect(screen.queryByText("Загрузка справочников…")).toBeNull(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Сформировать отчёт" }));
  await screen.findByText("Тепловая карта спроса");
  fireEvent.click(screen.getByRole("button", { name: "Магазин", exact: true }));
  fireEvent.click(screen.getByText("A"));
  expect(calls).toHaveLength(1);
  expect(screen.getByText(/Параметры изменены/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Чашки", exact: true }));
  expect(screen.getByRole("dialog").textContent).not.toContain("МагазиныA");
  expect(calls).toHaveLength(1);
});
