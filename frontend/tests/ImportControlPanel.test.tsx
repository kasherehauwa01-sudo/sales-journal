import { StrictMode } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  ImportControlPanel,
  missingFileLabel,
} from "../src/components/ImportControlPanel";
const config = {
  enabled: false,
  automatic: false,
  email_enabled: false,
  start_date: null,
  day_offset: null,
  marker_template: null,
  recipients: "",
  revision: 1,
};
const issue = {
  id: 1,
  sales_date: "2026-11-08",
  department_name: "Synthetic department",
  department_id: 1,
  expected_filename: "fixture_08.11.2026.html",
  status: "missing",
  checked_at: "2026-11-09T00:00:00Z",
};
let fetchMock: ReturnType<typeof vi.fn>;
let counts = { missing: 2, error: 0 };
let failed = false;
beforeEach(() => {
  counts = { missing: 2, error: 0 };
  failed = false;
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (failed)
      return new Response(
        JSON.stringify({ detail: "Сервис проверки недоступен" }),
        { status: 503 },
      );
    const path = String(url);
    let value: unknown = {};
    if (path.includes("/status"))
      value = { enabled: false, latest: null, open_counts: counts };
    else if (path.includes("/settings")) value = config;
    else if (path.includes("/departments")) value = [];
    else if (path.includes("/issues?"))
      value = {
        items: [issue],
        total: 51,
        page: new URL(path, "http://fixture").searchParams.get("page") || 1,
        last_checked_at: issue.checked_at,
      };
    else if (path.includes("/notices"))
      value = { items: [], total: 0, page: 1 };
    else if (init?.method === "POST") value = [];
    return new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const posts = () =>
  fetchMock.mock.calls.filter(
    ([, init]) => init?.method === "POST" || init?.method === "PUT",
  );
it("uses correct Russian plural forms", () => {
  expect([1, 2, 5, 11, 21, 22].map(missingFileLabel)).toEqual([
    "Не загружен 1 файл",
    "Не загружено 2 файла",
    "Не загружено 5 файлов",
    "Не загружено 11 файлов",
    "Не загружен 21 файл",
    "Не загружено 22 файла",
  ]);
});
it("opens disabled, does not start an import or email even in StrictMode", async () => {
  render(
    <StrictMode>
      <ImportControlPanel />
    </StrictMode>,
  );
  await screen.findByText("Контроль отключён");
  expect(posts()).toHaveLength(0);
  expect(
    screen.getByRole("button", { name: "Не загружено 2 файла" }),
  ).toBeTruthy();
});
it("opens all historical unresolved issues without a today-only filter and restores focus", async () => {
  render(<ImportControlPanel />);
  const trigger = await screen.findByRole("button", {
    name: "Не загружено 2 файла",
  });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", {
    name: "Отсутствующие файлы импорта",
  });
  await within(dialog).findByText("Synthetic department");
  const call = fetchMock.mock.calls.find(([url]) =>
    String(url).includes("/issues?"),
  );
  expect(String(call?.[0])).not.toContain("date_from");
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(trigger);
});
it("filters and paginates on the server, and refresh checks never reimport", async () => {
  render(<ImportControlPanel />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Не загружено 2 файла" }),
  );
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByText("Synthetic department");
  fireEvent.click(within(dialog).getByRole("button", { name: "Далее" }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("page=2")),
    ).toBe(true),
  );
  fireEvent.change(within(dialog).getByLabelText("С даты"), {
    target: { value: "2026-11-01" },
  });
  fireEvent.change(within(dialog).getByLabelText("По дату"), {
    target: { value: "2026-11-08" },
  });
  fireEvent.change(within(dialog).getByLabelText("Статус"), {
    target: { value: "error" },
  });
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Применить фильтры" }),
  );
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url]) =>
          String(url).includes("date_from=2026-11-01") &&
          String(url).includes("status=error"),
      ),
    ).toBe(true),
  );
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Обновить проверку" }),
  );
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(String(posts()[0][0])).toContain("/import-control/check");
  expect(new Headers(posts()[0][1]?.headers).get("Content-Type")).toBe(
    "application/json",
  );
  expect(JSON.parse(String(posts()[0][1]?.body))).toEqual({
    date_from: "2026-11-01",
    date_to: "2026-11-08",
  });
});
it("shows API failures without automatic retries", async () => {
  failed = true;
  render(<ImportControlPanel />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Сервис проверки недоступен",
  );
  expect(posts()).toHaveLength(0);
  expect(screen.getByText("Проверка недоступна")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
it("provides department editing and an honest empty state", async () => {
  counts = { missing: 0, error: 0 };
  render(<ImportControlPanel />);
  await screen.findByText("Контроль отключён");
  fireEvent.click(
    screen.getByRole("button", { name: "Подразделения", exact: true }),
  );
  expect(screen.getByText(/Подразделения не настроены/)).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Добавить подразделение" }),
  );
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Код подразделения"), {
    target: { value: "SYNTH" },
  });
  fireEvent.change(within(dialog).getByLabelText("Название"), {
    target: { value: "Synthetic" },
  });
  fireEvent.change(
    within(dialog).getByLabelText("Точное имя или шаблон с {date}"),
    { target: { value: "fixture_{date}.html" } },
  );
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Сохранить подразделение" }),
  );
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(JSON.parse(String(posts()[0][1]?.body)).code).toBe("SYNTH");
});

it("rechecks unresolved history without a date filter instead of launching an import", async () => {
  render(<ImportControlPanel />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Не загружено 2 файла" }),
  );
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByText("Synthetic department");
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Обновить проверку" }),
  );
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(String(posts()[0][0])).toContain("/import-control/check-open");
});

it("keeps a useful history view after closing its registry dialog", async () => {
  render(<ImportControlPanel />);
  await screen.findByText("Контроль отключён");
  fireEvent.click(
    screen.getByRole("button", { name: "История пропусков", exact: true }),
  );
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть окно" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(
    screen.getByRole("heading", { name: "История пропусков" }),
  ).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Открыть реестр пропусков" }),
  );
  await screen.findByRole("dialog");
  expect(posts()).toHaveLength(0);
});
