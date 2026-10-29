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
