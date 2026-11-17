import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SalesDynamicsReportPage } from "../src/pages/SalesDynamicsReportPage";
import { StoreAnalyticsReportPage } from "../src/pages/StoreAnalyticsReportPage";
vi.mock("recharts", () => {
  const Box = ({ children }: any) => <div>{children}</div>;
  return { ResponsiveContainer: Box, LineChart: Box, BarChart: Box, CartesianGrid: () => null,
    Legend: () => null, Line: () => null, Bar: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

for (const [name, Page] of [["Динамика продаж", SalesDynamicsReportPage], ["Аналитика по магазинам", StoreAnalyticsReportPage]] as const) {
  it(`${name}: loads catalogue brands, searches and preserves checked values without calculating a report`, async () => {
    const fetch = vi.fn(async (url: string) => {
      const parsed = new URL(url, "http://test");
      let result: unknown = [];
      if (parsed.pathname.endsWith("/filters")) result = { departments: [] };
      if (parsed.pathname.endsWith("/catalog-options/brand")) {
        const term = parsed.searchParams.get("search") || "";
        result = ["Альфа", "Бета", "Гамма"].filter(value => value.toLowerCase().includes(term.toLowerCase()));
      }
      return new Response(JSON.stringify(result), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<Page />);
    const label = screen.getByText("Бренд", { selector: "label" });
    fireEvent.click(within(label).getByRole("button"));
    const alpha = await within(label).findByRole("checkbox", { name: "Альфа" });
    expect(within(label).getByRole("textbox", { name: "" })).toBeTruthy();
    fireEvent.click(alpha);
    fireEvent.change(within(label).getByPlaceholderText("Поиск"), { target: { value: "Бета" } });
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => new URL(url, "http://test").searchParams.get("search") === "Бета")).toBe(true));
    fireEvent.click(await within(label).findByRole("checkbox", { name: "Бета" }));
    fireEvent.change(within(label).getByPlaceholderText("Поиск"), { target: { value: "" } });
    expect((await within(label).findByRole("checkbox", { name: "Альфа" }) as HTMLInputElement).checked).toBe(true);
    expect((within(label).getByRole("checkbox", { name: "Бета" }) as HTMLInputElement).checked).toBe(true);
    expect(within(label).getByText("Выбрано: 2")).toBeTruthy();
    expect(fetch.mock.calls.some(([url]) => url.includes("/reports/brands"))).toBe(false);
    expect(fetch.mock.calls.some(([url]) => /\/reports\/(store-analytics|sales-dynamics)\?/.test(url))).toBe(false);
    fireEvent.click(within(label).getByRole("button", { name: "Сбросить" }));
    expect(within(label).getByText("Все бренды")).toBeTruthy();
  });

  it(`${name}: shows a catalogue failure explicitly`, async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/catalog-options/brand")
      ? new Response(JSON.stringify({ detail: "Каталог недоступен" }), { status: 502 })
      : new Response(JSON.stringify(url.includes("/filters") ? { departments: [] } : []))));
    render(<Page />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Не удалось загрузить бренды: Каталог недоступен");
    fireEvent.click(within(screen.getByText("Бренд", { selector: "label" })).getByRole("button"));
    expect(screen.getByText("Справочник брендов недоступен")).toBeTruthy();
  });
}

for (const [name, Page, endpoint] of [
  ["Динамика продаж", SalesDynamicsReportPage, "/reports/sales-dynamics"],
  ["Аналитика по магазинам", StoreAnalyticsReportPage, "/reports/store-analytics"],
] as const) {
  it(`${name}: submits the leaf values of a selected category group only when requested`, async () => {
    const fetch = vi.fn(async (url: string) => {
      const path = new URL(url, "http://test").pathname;
      if (path.endsWith(endpoint)) {
        return new Response(JSON.stringify({ detail: "Test response" }), { status: 400 });
      }
      const data = path.endsWith("/catalog/category-tree") ? {
        nodes: [{
          id: "tableware", name: "Посуда", children: [
            { id: "cups", name: "Кружки", value: "КРУЖКИ", children: [] },
            { id: "plates", name: "Тарелки", value: "ТАРЕЛКИ", children: [] },
          ],
        }],
      } : path.endsWith("/filters") ? { departments: [] } : [];
      return new Response(JSON.stringify(data));
    });
    vi.stubGlobal("fetch", fetch);
    const reportCalls = () => fetch.mock.calls
      .map(([url]) => new URL(url, "http://test"))
      .filter(({ pathname }) => pathname.endsWith(endpoint));
    render(<Page />);

    fireEvent.click(screen.getByRole("button", { name: "Категория" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Посуда" }));
    expect(reportCalls()).toHaveLength(0);
    expect(screen.getByText("Категория: выбрано 2")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Сформировать отчёт" }));
    await waitFor(() => expect(reportCalls()).toHaveLength(1));
    expect(reportCalls()[0].searchParams.getAll("categories").sort()).toEqual(["КРУЖКИ", "ТАРЕЛКИ"]);
    await screen.findByText("Test response");
  });
}
