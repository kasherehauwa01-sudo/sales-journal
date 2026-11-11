import { StrictMode } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ImportHistoryPanel } from "../src/components/ImportHistoryPanel";
const departments = [
  {
    id: 1,
    name: "Fixture",
    code: "SYNTH",
    rules: [{ start_date: "2019-09-21" }],
  },
];
let fetchMock: ReturnType<typeof vi.fn>;
let job: any,
  failed = false;
beforeEach(() => {
  job = { status: "not_started", checked: 0, total: 0, plan: [] };
  failed = false;
  fetchMock = vi.fn(async (url: string) => {
    if (failed)
      return new Response(JSON.stringify({ detail: "Нет доступа к сверке" }), {
        status: 503,
      });
    const path = String(url);
    let value: any = job;
    if (path.includes("/start")) {
      job = {
        status: "running",
        checked: 0,
        total: 2609,
        plan: [
          {
            department_id: 1,
            first_rule_date: "2019-09-21",
            date_from: "2019-09-21",
            date_to: "2026-11-11",
          },
        ],
      };
      value = job;
    }
    if (path.includes("/pause")) {
      job = { ...job, status: "paused" };
      value = job;
    }
    if (path.includes("/resume")) {
      job = { ...job, status: "running", checked: 1 };
      value = job;
    }
    if (path.includes("/results?"))
      value = {
        page: 1,
        total: 1,
        items: [
          {
            sales_date: "2026-11-09",
            department_id: 1,
            department_name: "Fixture",
            department_code: "SYNTH",
            expected_filename: "fixture_09.11.2026.html",
            status: "missing",
            checked_at: "2026-11-11T08:00:00Z",
          },
        ],
      };
    if (path.includes("/preview"))
      value = {
        token: "a".repeat(64),
        items: [
          {
            rule_id: 10,
            department_code: "SYNTH",
            start_date: "2019-09-21",
            end_date: null,
            before: "fixture_09.11.2026.html",
            after: "fixture_{date}.html",
          },
        ],
      };
    if (path.includes("/apply")) value = { updated: 1 };
    return new Response(JSON.stringify(value), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const writes = () =>
  fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
it("does not start reconciliation or repair on mount, including StrictMode", async () => {
  render(
    <StrictMode>
      <ImportHistoryPanel departments={departments} />
    </StrictMode>,
  );
  await screen.findByText("21.09.2019");
  expect(writes()).toHaveLength(0);
  expect(
    (screen.getByText("Начать сверку с правил") as HTMLButtonElement).disabled,
  ).toBe(true);
});
it("starts once with approval and pauses/resumes saved progress", async () => {
  render(<ImportHistoryPanel departments={departments} />);
  await screen.findByText("21.09.2019");
  fireEvent.click(
    screen.getByLabelText(
      "Подтверждаю запуск или новый пересчёт выбранной истории",
    ),
  );
  fireEvent.click(screen.getByText("Начать сверку с правил"));
  fireEvent.click(screen.getByText("Начать сверку с правил"));
  await waitFor(() => expect(writes()).toHaveLength(1));
  await screen.findByText(/Проверено дней подразделений: 0 из 2609/);
  fireEvent.click(screen.getByText("Приостановить сверку"));
  await screen.findByText(/Состояние: Приостановлена/);
  fireEvent.click(screen.getByText("Продолжить сверку"));
  await screen.findByText(/Проверено дней подразделений: 1 из 2609/);
  expect(writes()).toHaveLength(3);
});
it("filters paginated metadata without triggering import", async () => {
  render(<ImportHistoryPanel departments={departments} />);
  await screen.findByText("21.09.2019");
  fireEvent.change(
    screen.getByLabelText("Подразделение для исторической сверки"),
    { target: { value: "1" } },
  );
  fireEvent.change(screen.getByLabelText("Статус файла"), {
    target: { value: "missing" },
  });
  fireEvent.click(screen.getByText("Показать результаты сверки"));
  await screen.findByText("fixture_09.11.2026.html");
  expect(
    fetchMock.mock.calls.some(
      ([url]) =>
        String(url).includes("department_id=1") &&
        String(url).includes("status=missing"),
    ),
  ).toBe(true);
  expect(writes()).toHaveLength(0);
});
it("repair needs preview, selected rules, reason and approval", async () => {
  render(<ImportHistoryPanel departments={departments} />);
  await screen.findByText("21.09.2019");
  fireEvent.click(screen.getByText("Подготовить отчёт исправления шаблонов"));
  await screen.findByText("fixture_{date}.html");
  expect(writes()).toHaveLength(0);
  const apply = screen.getByText(
    "Применить выбранные исправления",
  ) as HTMLButtonElement;
  expect(apply.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("Исправить правило 10"));
  fireEvent.change(screen.getByLabelText("Причина исправления"), {
    target: { value: "Проверено администратором" },
  });
  fireEvent.click(
    screen.getByLabelText(
      "Подтверждаю выбранные исправления исторических шаблонов",
    ),
  );
  fireEvent.click(apply);
  await waitFor(() => expect(writes()).toHaveLength(1));
  expect(JSON.parse(String(writes()[0][1]?.body))).toMatchObject({
    rule_ids: [10],
    approve: true,
    token: "a".repeat(64),
  });
});
it("shows API errors and restores controls for manual retry", async () => {
  render(<ImportHistoryPanel departments={departments} />);
  await screen.findByText("21.09.2019");
  failed = true;
  fireEvent.click(screen.getByText("Обновить прогресс"));
  await screen.findByRole("alert");
  expect(
    (screen.getByText("Обновить прогресс") as HTMLButtonElement).disabled,
  ).toBe(false);
  failed = false;
  fireEvent.click(screen.getByText("Обновить прогресс"));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(writes()).toHaveLength(0);
});
