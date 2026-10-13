import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SeasonalityReportPage } from "../src/pages/SeasonalityReportPage";
vi.mock("recharts", () => {
  const Box = ({ children }: any) => <div>{children}</div>;
  return {
    ResponsiveContainer: Box,
    LineChart: Box,
    Line: () => null,
    CartesianGrid: () => null,
    Legend: () => null,
    Tooltip: () => null,
    XAxis: () => null,
    YAxis: () => null,
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const meta = { ready: true, last_success: "2026-10-13", phase: "idle" };
const season = {
  status: "Сезон сейчас",
  confidence: 80,
  reason: "Модель по всей истории",
  period: "10–12",
};
const product = {
  identity: "a".repeat(64),
  product_key: "code:001",
  years: [2015, 2024, 2025],
  seasonality: season,
};
const profile = {
  years: product.years,
  indices: [1],
  historical_indices: [1],
  windows: [],
  historical_windows: [],
  confidence: 80,
  stability: 90,
  trend_percent: 10,
  annual: product.years.map((year) => ({
    year,
    eligible: true,
    total_units: 100,
    peak_month: 11,
    observed_months: [1, 2],
    windows: [],
    weight: year === 2025 ? 1 : 0.5,
  })),
};
it("reads all-history results explicitly and display/year filters never request a model calculation", async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url.includes("/filters"))
      return new Response(JSON.stringify({ departments: ["Магазин"] }));
    if (url.includes(`/seasonality/${product.identity}`))
      return new Response(
        JSON.stringify({
          product_key: product.product_key,
          metadata: meta,
          profile,
          seasonality: season,
          display_note: "Только отображение",
          months: [
            {
              year: 2025,
              month: 11,
              units: "100.000",
              incomplete_source: false,
            },
          ],
          next_cursor: null,
        }),
      );
    return new Response(
      JSON.stringify({ items: [product], next_cursor: null, metadata: meta }),
    );
  });
  vi.stubGlobal("fetch", fetch);
  render(<SeasonalityReportPage />);
  expect(
    fetch.mock.calls.filter(([url]) => url.includes("/seasonality")),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Сформировать отчёт" }));
  fireEvent.click(await screen.findByRole("button", { name: "001" }));
  await screen.findByText("Модель по всей истории");
  expect(
    (screen.getByLabelText("Период отображения") as HTMLSelectElement).value,
  ).toBe("all");
  fireEvent.change(screen.getByLabelText("Период отображения"), {
    target: { value: "12" },
  });
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.includes("date_from="))).toBe(
      true,
    ),
  );
  expect(screen.getByText("Модель по всей истории")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Магазин (только отображение)"), {
    target: { value: "Магазин" },
  });
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.includes("store="))).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: /Выбрано: 2/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "2015" }));
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.includes("years=2015"))).toBe(
      true,
    ),
  );
  expect(
    fetch.mock.calls.filter(([url]) => /\/seasonality\?/.test(url)),
  ).toHaveLength(1);
  expect(
    fetch.mock.calls.every(
      (call: any) => !call[1]?.method || call[1].method === "GET",
    ),
  ).toBe(true);
});
it("does not disguise unfinished calculation as valid seasonal evidence", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.includes("/filters")
              ? { departments: [] }
              : {
                  items: [],
                  metadata: { ready: false, phase: "scan" },
                  next_cursor: null,
                },
          ),
        ),
    ),
  );
  render(<SeasonalityReportPage />);
  fireEvent.click(screen.getByRole("button", { name: "Сформировать отчёт" }));
  expect(await screen.findByText(/Актуальный расчёт не завершён/)).toBeTruthy();
});
