import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import {
  SalesControlPanel,
  defaultPeriod,
} from "../src/components/SalesControlPanel";
import { SalesNotificationSettings } from "../src/components/SalesNotificationSettings";
let fetchMock: ReturnType<typeof vi.fn>,
  fail = false,
  posts: any[] = [];
const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const initial = {
  enabled: false,
  send_time: "09:00",
  recipients: [],
  revision: 1,
};
beforeEach(() => {
  fail = false;
  posts = [];
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method) {
      posts.push([url, init]);
      if (fail) return response({ detail: "Временная ошибка" }, 503);
      if (init.method === "PUT")
        return response({ ...JSON.parse(init.body as string), revision: 2 });
      return response({ changed: 1 });
    }
    if (url.endsWith("/notification-settings")) return response(initial);
    if (url.endsWith("/notification-status")) return response(null);
    if (url.endsWith("/departments"))
      return response([{ department: "Магазин" }]);
    if (fail) return response({ detail: "Сервер недоступен" }, 503);
    return response({
      items: [
        {
          department: "Магазин",
          sales_date: "2026-11-09",
          status: "Продажи отсутствуют",
        },
      ],
      total: 51,
      page: 1,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const panel = () =>
  render(
    <MemoryRouter
      initialEntries={["/imports?date_from=2026-11-01&date_to=2026-11-11"]}
    >
      <SalesControlPanel />
    </MemoryRouter>,
  );
it("loads actual sales automatically without old file control or writes", async () => {
  panel();
  await screen.findByText("Продажи отсутствуют");
  expect(posts).toHaveLength(0);
  expect((screen.getByLabelText("Дата с") as HTMLInputElement).value).toBe(
    "2026-11-01",
  );
  expect(screen.getAllByRole("columnheader").map((x) => x.textContent)).toEqual(
    ["", "Дата ↓", "Подразделение ", "Статус"],
  );
  expect(
    fetchMock.mock.calls.every(([url]) => !url.includes("import-control")),
  ).toBe(true);
});
it("selects the page, confirms logical hide and never calls sales deletion", async () => {
  panel();
  await screen.findByText("Продажи отсутствуют");
  expect(
    (screen.getByRole("button", { name: "Удалить" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(
    screen.getByLabelText("Выделить все строки текущей страницы"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain(
    "Сами продажи и подразделения не изменятся",
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  expect(posts).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Удалить" }),
  );
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0][0]).toContain("/sales-control/exclusions");
  expect(JSON.parse(posts[0][1].body)).toEqual({
    approve: true,
    items: [{ department: "Магазин", sales_date: "2026-11-09" }],
  });
});
it("clears selection on filtering and pagination and supports restore", async () => {
  panel();
  await screen.findByText("Продажи отсутствуют");
  fireEvent.click(
    screen.getByLabelText("Выделить все строки текущей страницы"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Удалить" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true),
  );
  fireEvent.click(screen.getByLabelText("Показать скрытые"));
  await screen.findByRole("button", { name: "Восстановить" });
  await waitFor(() =>
    expect(
      (
        screen.getByLabelText(
          "Выделить все строки текущей страницы",
        ) as HTMLInputElement
      ).checked,
    ).toBe(false),
  );
  fireEvent.click(
    screen.getByLabelText("Выделить все строки текущей страницы"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Восстановить" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Восстановить",
    }),
  );
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0][1].method).toBe("DELETE");
});
it("debounces server search, clears it and does not retain selections", async () => {
  panel();
  await screen.findByText("Продажи отсутствуют");
  fireEvent.click(
    screen.getByLabelText("Выделить все строки текущей страницы"),
  );
  fireEvent.change(
    screen.getByPlaceholderText("Поиск по подразделению или дате"),
    { target: { value: "09.11" } },
  );
  expect(
    (screen.getByRole("button", { name: "Удалить" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([u]) => u.includes("search=09.11"))).toBe(
      true,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Очистить поиск" }));
  expect(
    (
      screen.getByPlaceholderText(
        "Поиск по подразделению или дате",
      ) as HTMLInputElement
    ).value,
  ).toBe("");
});
it("shows an error instead of false empty and retries manually", async () => {
  fail = true;
  panel();
  await screen.findByRole("alert");
  expect(screen.queryByText("Отсутствующих продаж не найдено")).toBeNull();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await screen.findByText("Продажи отсутствуют");
  expect(posts).toHaveLength(0);
});
it("defaults to 30 completed days independently of workstation timezone", () => {
  const { date_from, date_to } = defaultPeriod();
  expect(
    (new Date(date_to).getTime() - new Date(date_from).getTime()) / 86400000,
  ).toBe(29);
});
it("validates, deduplicates recipients and saves explicitly without sending", async () => {
  render(<SalesNotificationSettings onDirty={() => {}} />);
  await screen.findByLabelText("Время ежедневной проверки");
  expect(posts).toHaveLength(0);
  fireEvent.click(
    screen.getByLabelText("Автоматический контроль продаж и email-уведомления"),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Сохранить настройки",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.change(screen.getByLabelText("Получатели уведомлений"), {
    target: { value: "bad" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
  await screen.findByText("Проверьте адрес: bad");
  fireEvent.change(screen.getByLabelText("Получатели уведомлений"), {
    target: { value: "a@example.ru;A@example.ru" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
  expect(
    screen.getAllByRole("button", { name: /Удалить получателя/ }),
  ).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));
  await screen.findByText("Настройки уведомлений сохранены");
  expect(posts).toHaveLength(1);
  expect(posts[0][0]).toContain("/notification-settings");
  expect(posts[0][1].method).toBe("PUT");
});
it("retains unsaved notification values after failure", async () => {
  render(<SalesNotificationSettings onDirty={() => {}} />);
  await screen.findByLabelText("Время ежедневной проверки");
  fireEvent.change(screen.getByLabelText("Время ежедневной проверки"), {
    target: { value: "10:30" },
  });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Сохранить настройки" }));
  await screen.findByText("Временная ошибка");
  expect(
    (screen.getByLabelText("Время ежедневной проверки") as HTMLInputElement)
      .value,
  ).toBe("10:30");
  expect(screen.getByText("Есть несохранённые изменения")).toBeTruthy();
});
