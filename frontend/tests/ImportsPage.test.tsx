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
import { ImportsPage } from "../src/pages/ImportsPage";
const batch = (id: number, filename: string, status: string) => ({
  id,
  filename,
  status,
  file_size: 1024,
  uploaded_at: "2026-11-11T10:00:00Z",
  period_start: "2026-11-10",
  period_end: "2026-11-10",
  total_rows: 10,
  processed_rows: 10,
  added_rows: 8,
  duplicate_rows: 2,
  skipped_rows: 0,
  error_rows: status === "failed" ? 1 : 0,
  duration_ms: 100,
  errors: [],
});
const items = [
  batch(1, "Продажи_А.html", "completed"),
  batch(2, "Продажи_Б.xls", "failed"),
  batch(3, "Ожидание.xlsx", "queued"),
];
let fetchMock: ReturnType<typeof vi.fn>,
  holdUpload: boolean,
  finishUpload: (value: Response) => void,
  uploadFails: boolean,
  listFails: boolean;
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
beforeEach(() => {
  holdUpload = false;
  uploadFails = false;
  listFails = false;
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    if (path.endsWith("/api/imports") && init?.method === "POST") {
      if (holdUpload)
        return new Promise<Response>((resolve) => {
          finishUpload = resolve;
        });
      return uploadFails
        ? response({ detail: "Не удалось передать файл" }, 503)
        : response([]);
    }
    if (path.endsWith("/api/imports"))
      return listFails
        ? response({ detail: "Журнал временно недоступен" }, 503)
        : response(items);
    if (path.endsWith("/api/imports/2"))
      return response({
        ...items[1],
        error_text: "Ошибка формата строки",
        log_text: "Синтетический журнал",
        errors: [{ id: 1, row_number: 2, message: "Нет обязательного поля" }],
      });
    if (path.endsWith("/import-control/status"))
      return response({
        enabled: false,
        latest: null,
        open_counts: { missing: 0, error: 0 },
      });
    if (path.endsWith("/import-control/settings"))
      return response({
        enabled: false,
        automatic: false,
        email_enabled: false,
        start_date: null,
        recipients: "",
        revision: 1,
      });
    return response([]);
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
const openUpload = () =>
  fireEvent.click(screen.getByRole("button", { name: /^Загрузка файлов/ }));
const select = (...files: File[]) =>
  fireEvent.change(screen.getByLabelText("Файлы продаж"), {
    target: { files },
  });
it("opens daily control without starting an import, a historical job or a notice", async () => {
  render(
    <StrictMode>
      <ImportsPage />
    </StrictMode>,
  );
  await screen.findByText("Контроль отключён");
  expect(
    screen
      .getByRole("button", { name: "Контроль полноты" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.queryByRole("button", { name: "Начать импорт" })).toBeNull();
  expect(posts()).toHaveLength(0);
});
it("supports real drag and drop, rejects unsupported formats and edits the selection", async () => {
  render(<ImportsPage />);
  openUpload();
  const first = new File(["fixture"], "first.html"),
    second = new File(["fixture"], "second.xlsx");
  fireEvent.drop(screen.getByRole("button", { name: "Выбрать файлы продаж" }), {
    dataTransfer: {
      files: [first, second, new File(["fixture"], "wrong.txt")],
    },
  });
  await screen.findByText(/Остальные файлы не добавлены/);
  expect(posts()).toHaveLength(0);
  fireEvent.click(
    screen.getByRole("button", { name: "Убрать файл first.html" }),
  );
  expect(
    screen.queryByRole("button", { name: "Убрать файл first.html" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Убрать файл second.xlsx" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Очистить список" }));
  expect(
    (screen.getByRole("button", { name: "Начать импорт" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
it("sends one multipart request and keeps repeat clicks disabled until the response", async () => {
  holdUpload = true;
  render(<ImportsPage />);
  openUpload();
  select(new File(["fixture"], "first.html"));
  const button = screen.getByRole("button", { name: "Начать импорт" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect((posts()[0][1]?.body as FormData).getAll("files")).toHaveLength(1);
  expect(
    (
      screen.getByRole("button", {
        name: "Передаём файлы…",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  finishUpload(response([]));
  await screen.findByText("Файлы переданы на обработку");
  expect(
    screen.queryByRole("button", { name: "Убрать файл first.html" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Перейти к журналу" }));
  expect(
    await screen.findByRole("region", { name: "Журнал загрузок" }),
  ).toBeTruthy();
});
it("retains selected files and unsaved control settings when switching workspaces", async () => {
  render(<ImportsPage />);
  await screen.findByText("Контроль отключён");
  fireEvent.click(screen.getByRole("button", { name: "Настройки контроля" }));
  fireEvent.click(screen.getByLabelText("Автоматические проверки"));
  openUpload();
  select(new File(["fixture"], "selected.xls"));
  fireEvent.click(screen.getByRole("button", { name: "Журнал загрузок" }));
  openUpload();
  expect(
    screen.getByRole("button", { name: "Убрать файл selected.xls" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Контроль полноты" }));
  expect(
    (screen.getByLabelText("Автоматические проверки") as HTMLInputElement)
      .checked,
  ).toBe(true);
  expect(posts()).toHaveLength(0);
});
it("shows an upload failure without retries and allows a manual retry with the same files", async () => {
  uploadFails = true;
  render(<ImportsPage />);
  openUpload();
  select(new File(["fixture"], "retry.html"));
  fireEvent.click(screen.getByRole("button", { name: "Начать импорт" }));
  await screen.findByText("Не удалось передать файл");
  expect(posts()).toHaveLength(1);
  expect(
    screen.getByRole("button", { name: "Убрать файл retry.html" }),
  ).toBeTruthy();
  uploadFails = false;
  fireEvent.click(screen.getByRole("button", { name: "Начать импорт" }));
  await screen.findByText("Файлы переданы на обработку");
  expect(posts()).toHaveLength(2);
});
it("filters only the latest journal records and restores the list without writes", async () => {
  render(<ImportsPage />);
  fireEvent.click(screen.getByRole("button", { name: "Журнал загрузок" }));
  const journal = screen.getByRole("region", { name: "Журнал загрузок" });
  await within(journal).findByText("Продажи_А.html");
  fireEvent.change(within(journal).getByLabelText("Поиск по имени файла"), {
    target: { value: "ПРОДАЖИ" },
  });
  fireEvent.change(within(journal).getByLabelText("Статус обработки"), {
    target: { value: "completed" },
  });
  expect(within(journal).queryByText("Продажи_Б.xls")).toBeNull();
  expect(within(journal).getByText("Продажи_А.html")).toBeTruthy();
  fireEvent.change(within(journal).getByLabelText("Поиск по имени файла"), {
    target: { value: "missing" },
  });
  fireEvent.click(
    within(journal).getByRole("button", { name: "Сбросить фильтры журнала" }),
  );
  expect(within(journal).getByText("Продажи_Б.xls")).toBeTruthy();
  expect(posts()).toHaveLength(0);
});
it("distinguishes a failed journal request from an empty journal and permits manual reload", async () => {
  listFails = true;
  render(<ImportsPage />);
  fireEvent.click(screen.getByRole("button", { name: "Журнал загрузок" }));
  await screen.findByText("Журнал недоступен");
  expect(screen.queryByText("Загрузок пока нет")).toBeNull();
  listFails = false;
  fireEvent.click(
    screen.getByRole("button", { name: "Повторить загрузку журнала" }),
  );
  await screen.findByText("Продажи_А.html");
  expect(posts()).toHaveLength(0);
});
it("opens error details in a keyboard-closeable dialog and returns focus", async () => {
  render(<ImportsPage />);
  fireEvent.click(screen.getByRole("button", { name: "Журнал загрузок" }));
  const button = await screen.findByRole("button", { name: "Ошибки: 1" });
  button.focus();
  fireEvent.click(button);
  const dialog = await screen.findByRole("dialog", { name: "Детали ошибки" });
  expect(within(dialog).getByText("Нет обязательного поля")).toBeTruthy();
  fireEvent(dialog, new Event("cancel", { bubbles: true, cancelable: true }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(document.activeElement).toBe(button);
});
