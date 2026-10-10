import { StrictMode, type PropsWithChildren } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductAnalyticsPage } from "../src/pages/ProductAnalyticsPage";

vi.mock("recharts", () => {
  const Container = ({ children }: PropsWithChildren) => <div>{children}</div>;
  const Chart = ({
    children,
    data,
  }: PropsWithChildren<{ data: unknown[] }>) => (
    <div data-testid="sales-chart" data-points={data.length}>
      {children}
    </div>
  );
  const Nothing = () => null;
  return {
    ResponsiveContainer: Container,
    LineChart: Chart,
    CartesianGrid: Nothing,
    Legend: Nothing,
    Line: Nothing,
    Tooltip: Nothing,
    XAxis: Nothing,
    YAxis: Nothing,
  };
});
const product = {
  key: "code:001",
  article: "A-001",
  code: "001",
  name: "Тестовый товар",
  brand: "Бренд A",
  manufacturer: "Завод A",
  category: "Категория",
  subcategory: "Раздел A",
  material: "Стекло",
  revenue: 100,
  previous_revenue: 50,
  revenue_difference: 50,
  revenue_change: 100,
  units: 2,
  previous_units: 1,
  units_difference: 1,
  checks: 1,
  previous_checks: 1,
  checks_change: 0,
  average_price: 50,
  first_sale: "2025-01-01",
  last_sale: "2026-01-01",
  stocks: [{ warehouse: "Склад A", quantity: 7 }],
  prices: [{ price_type: "Розница", value: 55 }],
  properties: [{ name: "Вид товара", value: "10" }],
};
const report = {
  summary: {
    revenue: { current: 100, previous: 50, change_percent: 100 },
    units: { current: 2, previous: 1, change_percent: 100 },
    checks: { current: 1, previous: 1 },
    sku: { current: 1, previous: 1 },
    average_revenue_per_sku: 100,
    average_units_per_sku: 2,
    new: 0,
    growth: 1,
    decline: 0,
    stopped: 0,
  },
  items: [product],
  total: 1,
};
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
let post: ReturnType<typeof vi.fn>, fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  post = vi.fn(async (url: string) =>
    url.includes("/details")
      ? json({
          product,
          points: [{ period: "2026-01-01", revenue: 100, units: 2 }],
        })
      : url.endsWith("/export")
        ? new Response("xlsx")
        : json(report),
  );
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "POST") return post(url, init);
    if (url.endsWith("/filters"))
      return Promise.resolve(json({ departments: ["Магазин A"] }));
    if (url.includes("/catalog-status"))
      return Promise.resolve(json({ state: { completed: true, last_full_success_at: "2026-10-10", last_success_at: "2026-10-10" } }));
    if (url.includes("/manager-options"))
      return Promise.resolve(json(["Менеджер A"]));
    if (url.includes("/catalog-options/brand"))
      return Promise.resolve(json(["Бренд A"]));
    if (url.includes("/catalog-options/manufacturer"))
      return Promise.resolve(json(["Завод A"]));
    if (url.includes("/catalog-options/subcategory"))
      return Promise.resolve(json(["Раздел A"]));
    return Promise.resolve(json(["Подсказка товара"]));
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
const build = () => screen.getByRole("button", { name: "Сформировать отчёт" });
async function generate() {
  fireEvent.click(build());
  await screen.findByText("Тестовый товар");
}
async function choose(placeholder: string, value: string) {
  fireEvent.click(screen.getByText(placeholder).closest("button")!);
  fireEvent.click(await screen.findByLabelText(value));
  fireEvent.mouseDown(document.body);
}
const payload = (index = 0) => JSON.parse(post.mock.calls[index][1].body);

describe("explicit product analytics loading", () => {
  it("does not POST on opening, StrictMode remount or rerender", async () => {
    const page = render(
      <StrictMode>
        <ProductAnalyticsPage />
      </StrictMode>,
    );
    await screen.findByText("Сформируйте товарный отчёт");
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(0));
    page.rerender(
      <StrictMode>
        <ProductAnalyticsPage />
      </StrictMode>,
    );
    expect(post).not.toHaveBeenCalled();
    expect(
      (
        screen.getByRole("button", {
          name: "Выгрузить в Excel",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await generate();
    page.rerender(
      <StrictMode>
        <ProductAnalyticsPage />
      </StrictMode>,
    );
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("keeps all edited filters, periods and grouping as draft until one explicit click", async () => {
    render(<ProductAnalyticsPage />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("Артикул / код"), {
      target: { value: "001" },
    });
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "Подсказка" },
    });
    await choose("Все магазины", "Магазин A");
    await choose("Все бренды", "Бренд A");
    await choose("Все производители", "Завод A");
    await choose("Все менеджеры", "Менеджер A");
    await choose("Все подкатегории", "Раздел A");
    fireEvent.change(screen.getByLabelText("Группировать по"), {
      target: { value: "brand" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Прошлый месяц" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Произвольный период" }),
    );
    fireEvent.change(screen.getByLabelText("Дата от"), {
      target: { value: "2026-03-01" },
    });
    fireEvent.change(screen.getByLabelText("Дата до"), {
      target: { value: "2026-03-15" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));
    await screen.findByDisplayValue("Подсказка");
    expect(post).not.toHaveBeenCalled();
    await generate();
    expect(post).toHaveBeenCalledTimes(1);
    expect(payload()).toMatchObject({
      date_from: "2026-03-01",
      date_to: "2026-03-15",
      compare_from: "2026-02-01",
      compare_to: "2026-02-15",
      article: "001",
      search: "Подсказка",
      group_by: "brand",
      departments: ["Магазин A"],
      brands: ["Бренд A"],
      manufacturers: ["Завод A"],
      managers: ["Менеджер A"],
      subcategories: ["Раздел A"],
    });
  });
  it("takes one immutable snapshot and rejects a double click before rerender", async () => {
    const reply = deferred<Response>();
    post.mockReturnValueOnce(reply.promise);
    render(<ProductAnalyticsPage />);
    const button = build();
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(
      (
        screen.getByRole("button", {
          name: "Формируется отчёт…",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "Новый черновик" },
    });
    await act(async () => reply.resolve(json(report)));
    expect(payload().search).toBe("");
    expect(screen.getByText(/Фильтры изменены/)).toBeTruthy();
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("tabs, sort and limit are explicit and never start parallel POSTs", async () => {
    render(<ProductAnalyticsPage />);
    await generate();
    const tabs = [
      "Рост",
      "Падение",
      "Перестали продаваться",
      "Новые товары",
      "ТОП",
    ];
    for (const name of tabs)
      fireEvent.click(screen.getByRole("button", { name }));
    fireEvent.change(screen.getByDisplayValue("По выручке"), {
      target: { value: "units" },
    });
    fireEvent.change(screen.getByDisplayValue("20"), {
      target: { value: "50" },
    });
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "Не применять к вкладке" },
    });
    expect(post).toHaveBeenCalledTimes(1);
    const reply = deferred<Response>();
    post.mockReturnValueOnce(reply.promise);
    const update = screen.getByRole("button", { name: "Обновить результаты" });
    act(() => {
      fireEvent.click(update);
      fireEvent.click(update);
    });
    fireEvent.click(screen.getByRole("button", { name: "Рост" }));
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][0]).toContain(
      "section=top&sort_by=units&limit=50",
    );
    expect(payload(1).search).toBe("");
    await act(async () => reply.resolve(json(report)));
    expect(screen.getByText(/Показан раздел «ТОП», до 50 строк/)).toBeTruthy();
    expect(
      screen.getByText(/Вкладка, сортировка или число строк изменены/),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Обновить результаты" }),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
    expect(post.mock.calls[2][0]).toContain("section=growth");
  });
  it("503 has a friendly error, no automatic retry, and exact manual retry after an error", async () => {
    post.mockResolvedValueOnce(json({ detail: "wait_timeout" }, 503));
    render(<ProductAnalyticsPage />);
    fireEvent.click(build());
    await screen.findByText(/Сервер занят или недостаточно памяти/);
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "Другой черновик" },
    });
    await screen.findByDisplayValue("Другой черновик");
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
    await screen.findByText("Тестовый товар");
    expect(post).toHaveBeenCalledTimes(2);
    expect(payload(1)).toEqual(payload(0));
  });
  it("network failure has a manual retry and never repeats automatically", async () => {
    post.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<ProductAnalyticsPage />);
    fireEvent.click(build());
    await screen.findByText(/Не удалось связаться с сервером/);
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
    await screen.findByText("Тестовый товар");
    expect(post).toHaveBeenCalledTimes(2);
  });
  it("keeps prior KPI/results when an explicit refresh fails", async () => {
    render(<ProductAnalyticsPage />);
    await generate();
    post.mockResolvedValueOnce(json({ detail: "Ошибка сервиса" }, 502));
    fireEvent.click(build());
    await screen.findByText("Ошибка сервиса");
    expect(screen.getByText("Тестовый товар")).toBeTruthy();
    expect(screen.getByText("Выручка на SKU")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  });
  it("loads product fields and chart using report filters, not the edited draft", async () => {
    render(<ProductAnalyticsPage />);
    await generate();
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "Черновой поиск" },
    });
    fireEvent.change(screen.getByLabelText("Группировать по"), {
      target: { value: "brand" },
    });
    fireEvent.click(screen.getByText("Тестовый товар"));
    await screen.findByText("Склад A");
    expect(screen.getByText("Розница")).toBeTruthy();
    expect(screen.getByText("Акция месяца")).toBeTruthy();
    expect(screen.getByTestId("sales-chart").getAttribute("data-points")).toBe(
      "1",
    );
    expect(payload(1)).toMatchObject({
      search: "",
      group_by: "product",
      article_key: "code:001",
    });
    fireEvent.change(screen.getByLabelText("Артикул / код"), {
      target: { value: "edited" },
    });
    expect(post).toHaveBeenCalledTimes(2);
    fireEvent.click(
      within(screen.getByRole("group", { name: "Период графика" })).getByRole(
        "button",
        { name: "Квартал" },
      ),
    );
    await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
    expect(post.mock.calls[2][0]).toContain("group_by=week");
  });
  it("serializes chart requests, blocks export/report, and supports detail retry", async () => {
    render(<ProductAnalyticsPage />);
    await generate();
    const reply = deferred<Response>();
    post.mockReturnValueOnce(reply.promise);
    fireEvent.click(screen.getByText("Тестовый товар"));
    fireEvent.click(
      within(screen.getByRole("group", { name: "Период графика" })).getByRole(
        "button",
        { name: "Квартал" },
      ),
    );
    fireEvent.click(build());
    fireEvent.click(screen.getByRole("button", { name: "Выгрузить в Excel" }));
    expect(post).toHaveBeenCalledTimes(2);
    await act(async () => reply.resolve(json({}, 503)));
    fireEvent.click(
      screen.getByRole("button", { name: "Повторить загрузку карточки" }),
    );
    await screen.findByText("Склад A");
    expect(post).toHaveBeenCalledTimes(3);
  });
  it("exports the generated snapshot once, shows progress, and can retry failed Excel manually", async () => {
    render(<ProductAnalyticsPage />);
    await generate();
    fireEvent.change(screen.getByLabelText("Название"), {
      target: { value: "Черновик" },
    });
    const reply = deferred<Response>();
    post.mockReturnValueOnce(reply.promise);
    const button = screen.getByRole("button", { name: "Выгрузить в Excel" });
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    fireEvent.click(build());
    expect(post).toHaveBeenCalledTimes(2);
    expect(
      (
        screen.getByRole("button", {
          name: "Формируется Excel…",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(payload(1)).toEqual(payload(0));
    await act(async () => reply.resolve(json({}, 503)));
    await screen.findByText(/Сервер занят или недостаточно памяти/);
    fireEvent.click(screen.getByRole("button", { name: "Выгрузить в Excel" }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:report");
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  });
  it("aborts waiting without auto retry and warns that the backend may keep running", async () => {
    post.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          ),
        ),
    );
    render(<ProductAnalyticsPage />);
    fireEvent.click(build());
    fireEvent.click(
      screen.getByRole("button", { name: "Прекратить ожидание" }),
    );
    await screen.findByText(/Расчёт на сервере может продолжаться/);
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
    await screen.findByText("Тестовый товар");
    expect(post).toHaveBeenCalledTimes(2);
  });
  it("unmount aborts the pending browser request and late replies cannot update the page", async () => {
    const reply = deferred<Response>();
    post.mockReturnValueOnce(reply.promise);
    const page = render(<ProductAnalyticsPage />);
    fireEvent.click(build());
    const signal = post.mock.calls[0][1].signal as AbortSignal;
    page.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => reply.resolve(json(report)));
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("keeps the slot until an aborted request actually settles", async () => {
    const reply = deferred<Response>();
    post.mockReturnValueOnce(reply.promise);
    render(<ProductAnalyticsPage />);
    fireEvent.click(build());
    fireEvent.click(
      screen.getByRole("button", { name: "Прекратить ожидание" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Формируется отчёт…" }));
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => reply.resolve(json(report)));
    await screen.findByText(/Расчёт на сервере может продолжаться/);
    expect(screen.queryByText("Тестовый товар")).toBeNull();
  });
});


describe("local CatalogVR characteristics diagnostics", () => {
  it("shows an incomplete directory without launching a report", async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => url.includes("/catalog-status")
      ? Promise.resolve(json({ state: null })) : original(url, init));
    render(<ProductAnalyticsPage />);
    await screen.findByText(/Локальные характеристики товаров ещё не сверены полностью/);
    expect(post).not.toHaveBeenCalled();
  });
  it("shows an ambiguous identity and a report coverage warning", async () => {
    post.mockResolvedValue(json({ ...report, items: [{ ...product, catalog_status: "ambiguous" }],
      catalog: { warning: "Один товар требует проверки сопоставления", missing: 1, counts: { ambiguous: 1 } } }));
    render(<ProductAnalyticsPage />);
    fireEvent.click(build());
    await screen.findByText("Неоднозначное сопоставление");
    expect(screen.getByText("Один товар требует проверки сопоставления")).toBeTruthy();
    expect(post).toHaveBeenCalledTimes(1);
  });
});
