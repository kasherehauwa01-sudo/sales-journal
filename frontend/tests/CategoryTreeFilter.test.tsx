import { useState } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, it, expect, vi } from "vitest";
import {
  CategoryTreeFilter,
  searchCategories,
  type CategoryNode,
} from "../src/components/CategoryTreeFilter";
const nodes: CategoryNode[] = [
  {
    id: "p",
    name: "Посуда",
    children: [
      {
        id: "a",
        name: "Чашки",
        value: "Чашки",
        section: "Чашки",
        children: [],
      },
      {
        id: "b",
        name: "Тарелки",
        value: "Тарелки",
        section: "Тарелки",
        children: [],
      },
    ],
  },
];
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(
    async () => new Response(JSON.stringify({ nodes }), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Form() {
  const [value, set] = useState<string[]>([]);
  return (
    <>
      <CategoryTreeFilter value={value} onChange={set} />
      <output>{JSON.stringify(value)}</output>
    </>
  );
}
it("loads lazily, selects groups, shows partial choice and clears without calculation", async () => {
  render(<Form />);
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Категория" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "Посуда" }));
  expect(screen.getByText('["Чашки","Тарелки"]')).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Развернуть: Посуда" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Чашки" }));
  expect(
    (screen.getByRole("checkbox", { name: "Посуда" }) as HTMLInputElement)
      .indeterminate,
  ).toBe(true);
  expect(
    (screen.getByRole("checkbox", { name: "Тарелки" }) as HTMLInputElement)
      .checked,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Сбросить" }));
  expect(screen.getByText("[]")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("search reveals ancestors and group choice includes hidden children", async () => {
  render(<Form />);
  fireEvent.click(screen.getByRole("button", { name: "Категория" }));
  await screen.findByRole("checkbox", { name: "Посуда" });
  fireEvent.change(screen.getByRole("textbox", { name: "Поиск: Категория" }), {
    target: { value: "чашки" },
  });
  expect(screen.getByRole("checkbox", { name: "Чашки" })).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: "Тарелки" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Посуда" }));
  expect(screen.getByText('["Чашки","Тарелки"]')).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Категория" }),
  );
});
it("keeps real report-only values and safely retries directory errors", async () => {
  fetchMock.mockResolvedValueOnce(new Response("{}", { status: 502 }));
  render(
    <CategoryTreeFilter
      value={["Историческое"]}
      onChange={() => {}}
      options={["Без категории"]}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Категория" }));
  await screen.findByRole("alert");
  fireEvent.click(
    screen.getByRole("button", { name: "Развернуть: Другие значения отчёта" }),
  );
  expect(screen.getByRole("checkbox", { name: "Историческое" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await screen.findByRole("checkbox", { name: "Посуда" });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
});
it("search normalizes ё and spacing without changing selected values", () => {
  expect(
    searchCategories(
      [{ id: "x", name: "Ёлочные   украшения", children: [] }],
      "елочные украшения",
    ),
  ).toHaveLength(1);
  expect(searchCategories(nodes, "Посуда")[0].children).toHaveLength(2);
});
