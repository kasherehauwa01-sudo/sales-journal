import { StrictMode, type PropsWithChildren } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RfmReportPage } from "../src/pages/RfmReportPage";
import { ProductSalesReportPage } from "../src/pages/ProductSalesReportPage";
import { CategoryAnalyticsReportPage } from "../src/pages/CategoryAnalyticsReportPage";
import { PromotionCandidatesPage } from "../src/pages/PromotionCandidatesPage";
import { SalesDynamicsReportPage } from "../src/pages/SalesDynamicsReportPage";
import { StoreAnalyticsReportPage } from "../src/pages/StoreAnalyticsReportPage";
vi.mock("recharts", () => {
  const Box = ({ children }: PropsWithChildren) => <div>{children}</div>;
  const N = () => null;
  return {
    ResponsiveContainer: Box,
    LineChart: Box,
    BarChart: Box,
    PieChart: Box,
    CartesianGrid: N,
    Legend: N,
    Line: N,
    Bar: N,
    Pie: N,
    Cell: N,
    Tooltip: N,
    XAxis: N,
    YAxis: N,
  };
});
const product = {
  key: "code:1",
  code: "1",
  article: "A1",
  name: "Товар1",
  units: 5,
  revenue: 100,
  checks: 1,
  clients: 1,
  average_price: 20,
  discount_amount: 0,
  average_discount: 0,
  last_sale: "2026-01-10",
  revenue_share: 100,
};
const category = {
  category: "Категория1",
  current_revenue: 100,
  previous_revenue: 50,
  current_units: 5,
  current_checks: 1,
  current_average_check: 100,
  current_share: 100,
  previous_share: 100,
  share_change_pp: 0,
  revenue_change: 50,
  revenue_change_percent: 100,
  contribution_percent: 100,
};
const client = {
  client: "Клиент1",
  client_key: "client1",
  segment: "Новые",
  tags: [],
  r_score: 5,
  f_score: 3,
  m_score: 4,
  rfm_code: "534",
  last_purchase: "2026-01-10",
  first_purchase: "2026-01-01",
  recency_days: 1,
  frequency: 2,
  purchases_per_month: 2,
  average_interval: 1,
  cycle_overdue: 0,
  monetary: 100,
  average_check: 50,
  units: 5,
  unique_products: 1,
  departments_count: 1,
};
const period = { start: "2026-01-01", end: "2026-01-10" };
const metrics = Object.fromEntries(
  [
    "revenue",
    "units",
    "checks",
    "average_check",
    "categories",
    "sales_count",
    "items_count",
    "average_items_per_sale",
    "items",
    "items_per_check",
    "average_discount",
  ].map((k) => [
    k,
    { current: 100, previous: 50, difference: 50, change_percent: 100 },
  ]),
);
function fixture(path: string): unknown {
  const route = path.split("?")[0];
  if (route === "/filters") return { departments: ["Магазин1"] };
  if (route.endsWith("/managers") && !route.includes("product-sales"))
    return ["Менеджер1", "Менеджер2"];
  if (route.endsWith("/buyer-types")) return ["Розница"];
  if (route === "/reports/product-sales/sets")
    return [{ id: 1, name: "Набор1", products: [product] }];
  if (route === "/reports/rfm")
    return {
      kpi: { total_clients: 1 },
      segments: [
        {
          segment: "Новые",
          clients: 1,
          client_share: 1,
          revenue: 100,
          revenue_share: 1,
          average_check: 50,
          average_purchases: 2,
          average_recency: 1,
        },
      ],
      clients: {
        items: [client],
        total: 2,
        page: Number(new URLSearchParams(path.split("?")[1]).get("page") || 1),
        pages: 2,
      },
      settings: {
        new_days: 30,
        lost_days: 90,
        sleeping_cycle: 2,
        lost_cycle: 3,
        boundaries: {},
      },
      clients_vr: { metadata_available: true },
    };
  if (route.includes("/rfm/clients/"))
    return [{ month: "2026-01", revenue: 100, purchases: 2 }];
  if (route === "/reports/category-analytics")
    return {
      period,
      previous_period: period,
      metrics,
      categories: [category],
      category_options: ["Категория1"],
      structure: [],
      growth_drivers: [],
      decline_drivers: [],
    };
  if (route.includes("/category-analytics/") && route.endsWith("/details"))
    return {
      category,
      products: [{ ...product, share: 100, revenue_change: 50 }],
      group_by: "day",
      points: [],
    };
  if (route === "/reports/store-analytics")
    return {
      period,
      previous_period: period,
      metrics,
      stores: [{ store: "Магазин1", metrics }],
    };
  if (route.includes("/store-analytics/stores/"))
    return route.endsWith("/dynamics")
      ? { group_by: "day", points: [] }
      : { items: [] };
  if (route === "/reports/sales-dynamics")
    return {
      period,
      requested_period: period,
      previous_period: period,
      metrics,
      chart: { group_by: "day", points: [] },
    };
  if (route === "/reports/promotion-candidates")
    return {
      items: [
        {
          ...product,
          category: "Категория1",
          reasons: ["Продажи падают"],
          stop_factors: [],
          status: "Кандидат",
          units_30: 2,
          units_previous_30: 4,
          units_60_90: 5,
          monthly_units: 2,
        },
      ],
      total: 1,
      table_limit: 500,
      summary: { candidates: 1, stale: 0, excess: 0, decline: 1 },
      catalog_available: true,
      limitations: [],
    };
  if (route === "/reports/promotion-candidates/history")
    return { items: [], message: "История готова" };
  if (route === "/reports/product-sales/summary")
    return { current: { ...product, items_per_check: 5 } };
  if (route === "/reports/product-sales/products") return { items: [product] };
  if (route === "/reports/product-sales/details") return { items: [] };
  return [];
}
const json = (v: unknown, status = 200) =>
  new Response(JSON.stringify(v), { status });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
const pathOf = (url: string) => url.slice(url.indexOf("/api") + 4);
const main = (p: string) =>
  /^\/reports\/(rfm|category-analytics|store-analytics|sales-dynamics)(\?|$)/.test(
    p,
  ) ||
  /^\/reports\/promotion-candidates(\?|$|\/history)/.test(p) ||
  /^\/reports\/product-sales\/(summary|products\?|dynamics|managers|departments)/.test(
    p,
  );
let fetchMock: ReturnType<typeof vi.fn>,
  heavy: ReturnType<typeof vi.fn>,
  active: number,
  peak: number;
beforeEach(() => {
  active = peak = 0;
  heavy = vi.fn(async (p: string) => json(fixture(p)));
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const p = pathOf(String(url));
    if (main(p)) {
      active++;
      peak = Math.max(peak, active);
      return Promise.resolve(heavy(p, init)).finally(() => active--);
    }
    return Promise.resolve(json(fixture(p)));
  });
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:report"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const pages = [
  {
    name: "RFM",
    Component: RfmReportPage,
    label: "Клиент",
    value: "Клиент1",
    next: "Клиент2",
    count: 1,
  },
  {
    name: "Товары",
    Component: ProductSalesReportPage,
    label: "Менеджер",
    value: "Менеджер1",
    next: "Менеджер2",
    count: 5,
  },
  {
    name: "Категории",
    Component: CategoryAnalyticsReportPage,
    label: "Менеджер",
    value: "Менеджер1",
    next: "Менеджер2",
    count: 1,
  },
  {
    name: "Кандидаты",
    Component: PromotionCandidatesPage,
    label: "Бренд",
    value: "Бренд1",
    next: "Бренд2",
    count: 1,
  },
  {
    name: "Динамика",
    Component: SalesDynamicsReportPage,
    label: "Клиент",
    value: "Клиент1",
    next: "Клиент2",
    count: 1,
  },
  {
    name: "Магазины",
    Component: StoreAnalyticsReportPage,
    label: "Менеджер",
    value: "Менеджер1",
    next: "Менеджер2",
    count: 1,
  },
];
const build = () => screen.getByRole("button", { name: "Сформировать отчёт" });
async function prepare(name: string) {
  if (name === "Товары")
    fireEvent.click(await screen.findByRole("button", { name: /Набор1/ }));
  else if (name === "RFM")
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  else await screen.findByRole("option", { name: "Менеджер1" });
}
async function finish(count: number) {
  await waitFor(() => expect(heavy).toHaveBeenCalledTimes(count));
  await waitFor(() =>
    expect((build() as HTMLButtonElement).disabled).toBe(false),
  );
}
for (const page of pages)
  describe(page.name, () => {
    it("opens/edits/rerenders without calculations", async () => {
      const { Component } = page;
      const view = render(
        <StrictMode>
          <Component />
        </StrictMode>,
      );
      await prepare(page.name);
      expect(screen.getByText("Сформируйте отчёт")).toBeTruthy();
      fireEvent.change(screen.getByLabelText(page.label), {
        target: { value: page.value },
      });
      view.rerender(
        <StrictMode>
          <Component />
        </StrictMode>,
      );
      expect(heavy).not.toHaveBeenCalled();
    });
    it("captures one snapshot and rejects double clicks, including before rerender", async () => {
      const { Component } = page;
      render(<Component />);
      await prepare(page.name);
      fireEvent.change(screen.getByLabelText(page.label), {
        target: { value: page.value },
      });
      const reply = deferred<Response>();
      heavy.mockReturnValueOnce(reply.promise);
      const b = build();
      act(() => {
        fireEvent.click(b);
        fireEvent.click(b);
      });
      expect(heavy).toHaveBeenCalledTimes(1);
      expect(
        (
          screen.getByRole("button", {
            name: "Формируется отчёт…",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
      fireEvent.change(screen.getByLabelText(page.label), {
        target: { value: page.next },
      });
      await act(async () =>
        reply.resolve(json(fixture(heavy.mock.calls[0][0]))),
      );
      await finish(page.count);
      expect(peak).toBe(1);
      for (const [p, init] of heavy.mock.calls) {
        const sent = init?.body ? String(init.body) : decodeURIComponent(p);
        expect(sent).toContain(page.value);
        expect(sent).not.toContain(page.next);
      }
      expect(screen.getByText(/Параметры изменены/)).toBeTruthy();
    });
    it("503 has no automatic retry and manual retry uses the original snapshot", async () => {
      const { Component } = page;
      render(<Component />);
      await prepare(page.name);
      fireEvent.change(screen.getByLabelText(page.label), {
        target: { value: page.value },
      });
      heavy.mockResolvedValueOnce(json({}, 503));
      fireEvent.click(build());
      await screen.findByText(/Сервер занят или недостаточно памяти/);
      fireEvent.change(screen.getByLabelText(page.label), {
        target: { value: page.next },
      });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 350));
      });
      expect(heavy).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
      await finish(page.count + 1);
      for (const [p, init] of heavy.mock.calls)
        expect(
          init?.body ? String(init.body) : decodeURIComponent(p),
        ).toContain(page.value);
    });
  });
it("history tab is explicitly loaded without simultaneously calculating candidates", async () => {
  render(<PromotionCandidatesPage />);
  fireEvent.click(
    screen.getByRole("button", { name: "История эффективности акций" }),
  );
  expect(heavy).not.toHaveBeenCalled();
  fireEvent.click(build());
  await screen.findByText("История готова");
  expect(heavy).toHaveBeenCalledTimes(1);
  expect(heavy.mock.calls[0][0]).toBe("/reports/promotion-candidates/history");
  fireEvent.click(screen.getByRole("button", { name: "Кандидаты" }));
  expect(heavy).toHaveBeenCalledTimes(1);
  fireEvent.click(build());
  await screen.findByText("Товар1");
  expect(heavy).toHaveBeenCalledTimes(2);
});
it("RFM pagination/history/Excel use the displayed report rather than draft filters", async () => {
  render(<RfmReportPage />);
  fireEvent.change(screen.getByLabelText("Клиент"), {
    target: { value: "Клиент1" },
  });
  fireEvent.click(build());
  await finish(1);
  fireEvent.change(screen.getByLabelText("Клиент"), {
    target: { value: "Черновик" },
  });
  fireEvent.click(screen.getByRole("button", { name: "→" }));
  await finish(2);
  expect(decodeURIComponent(heavy.mock.calls[1][0])).toContain(
    "search=Клиент1",
  );
  expect(heavy.mock.calls[1][0]).toContain("page=2");
  fireEvent.click(screen.getByText("Клиент1"));
  await screen.findByText("Карточка клиента");
  fireEvent.click(screen.getByRole("button", { name: /Закрыть/ }));
  fireEvent.click(screen.getByRole("button", { name: "Полный Excel" }));
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
  const exports = fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/rfm/export"),
  );
  expect(exports).toHaveLength(1);
  expect(decodeURIComponent(exports[0][0])).toContain("search=Клиент1");
});
it("dynamics grouping is draft and metric switching is local", async () => {
  render(<SalesDynamicsReportPage />);
  fireEvent.click(build());
  await finish(1);
  fireEvent.click(screen.getByRole("button", { name: "По неделям" }));
  fireEvent.change(screen.getByDisplayValue("Выручка"), {
    target: { value: "items_count" },
  });
  expect(heavy).toHaveBeenCalledTimes(1);
  fireEvent.click(build());
  await finish(2);
  expect(heavy.mock.calls[1][0]).toContain("group_by=week");
});
it("product-sales grouping waits for the button and its five requests are sequential", async () => {
  render(<ProductSalesReportPage />);
  await prepare("Товары");
  fireEvent.click(build());
  await finish(5);
  fireEvent.click(screen.getAllByRole("button", { name: "Неделя" })[1]);
  expect(heavy).toHaveBeenCalledTimes(5);
  fireEvent.click(build());
  await finish(10);
  expect(heavy.mock.calls[7][0]).toContain("group_by=week");
  expect(peak).toBe(1);
});
it("product-sales validates a missing selection before requesting reports", async () => {
  render(<ProductSalesReportPage />);
  fireEvent.click(build());
  await screen.findByText("Выберите хотя бы один товар");
  expect(heavy).not.toHaveBeenCalled();
});
it("category details retain report filters when draft changes", async () => {
  render(<CategoryAnalyticsReportPage />);
  await screen.findByRole("option", { name: "Менеджер1" });
  fireEvent.change(screen.getByLabelText("Менеджер"), {
    target: { value: "Менеджер1" },
  });
  fireEvent.click(build());
  await finish(1);
  fireEvent.change(screen.getByLabelText("Менеджер"), {
    target: { value: "Менеджер2" },
  });
  fireEvent.click(screen.getByText("Категория1", { selector: "b" }));
  await screen.findByText("Детализация категории");
  const calls = () =>
    fetchMock.mock.calls.filter(([url]) => String(url).includes("/details?"));
  expect(calls()).toHaveLength(1);
  expect(decodeURIComponent(calls()[0][0])).toContain("manager=Менеджер1");
  fireEvent.change(screen.getByLabelText("Менеджер"), {
    target: { value: "" },
  });
  expect(calls()).toHaveLength(1);
});
it("store details use the report snapshot for both requests", async () => {
  render(<StoreAnalyticsReportPage />);
  await screen.findByRole("option", { name: "Менеджер1" });
  fireEvent.change(screen.getByLabelText("Менеджер"), {
    target: { value: "Менеджер1" },
  });
  fireEvent.click(build());
  await finish(1);
  fireEvent.change(screen.getByLabelText("Менеджер"), {
    target: { value: "Менеджер2" },
  });
  fireEvent.click(screen.getByText("Магазин1", { selector: "b" }));
  await screen.findByText("Детализация магазина");
  const calls = fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/store-analytics/stores/"),
  );
  expect(calls).toHaveLength(2);
  for (const [url] of calls)
    expect(decodeURIComponent(url)).toContain("manager=Менеджер1");
});
it("cancelled responses cannot become generated results", async () => {
  const reply = deferred<Response>();
  heavy.mockReturnValueOnce(reply.promise);
  render(<SalesDynamicsReportPage />);
  fireEvent.click(build());
  const signal = heavy.mock.calls[0][1].signal as AbortSignal;
  fireEvent.click(screen.getByRole("button", { name: "Прекратить ожидание" }));
  expect(signal.aborted).toBe(true);
  await act(async () =>
    reply.resolve(json(fixture("/reports/sales-dynamics"))),
  );
  await screen.findByText(/Расчёт на сервере может продолжаться/);
  expect(heavy).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Сформируйте отчёт")).toBeTruthy();
});
for (const page of pages.filter((p) => p.name !== "Динамика"))
  it(`${page.name}: Excel is guarded and exports the successful snapshot`, async () => {
    const { Component } = page;
    render(<Component />);
    await prepare(page.name);
    fireEvent.change(screen.getByLabelText(page.label), {
      target: { value: page.value },
    });
    fireEvent.click(build());
    await finish(page.count);
    fireEvent.change(screen.getByLabelText(page.label), {
      target: { value: page.next },
    });
    const reply = deferred<Response>(),
      original = fetchMock.getMockImplementation()!;
    let first = true;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes("/export") && first) {
        first = false;
        return reply.promise;
      }
      return original(url, init);
    });
    const name =
      page.name === "RFM"
        ? "Полный Excel"
        : page.name === "Товары"
          ? "Выгрузить Excel"
          : "Выгрузить в Excel";
    const button = screen.getByRole("button", { name });
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    fireEvent.click(build());
    const calls = () =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/export"));
    expect(calls()).toHaveLength(1);
    const [url, init] = calls()[0];
    const sent = init?.body ? String(init.body) : decodeURIComponent(url);
    expect(sent).toContain(page.value);
    expect(sent).not.toContain(page.next);
    expect(screen.getByText("Формируется Excel…")).toBeTruthy();
    expect(heavy).toHaveBeenCalledTimes(page.count);
    await act(async () => reply.resolve(new Response("xlsx")));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:report");
  });
