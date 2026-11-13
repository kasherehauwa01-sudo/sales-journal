import {
  useEffect,
  useLayoutEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { useApi } from "../hooks/useApi";
export type CategoryNode = {
  id: string;
  name: string;
  value?: string;
  section?: string;
  children: CategoryNode[];
};
const normalized = (s: string) =>
  s.trim().replace(/\s+/g, " ").toLocaleLowerCase("ru-RU").replace(/ё/g, "е");
const values = (node: CategoryNode): string[] =>
  node.value ? [node.value] : [...new Set(node.children.flatMap(values))];
export function searchCategories(
  nodes: CategoryNode[],
  search: string,
): CategoryNode[] {
  const term = normalized(search);
  return nodes.flatMap((node) => {
    if (!term || normalized(node.name).includes(term)) return [node];
    const children = searchCategories(node.children, search);
    return children.length ? [{ ...node, children }] : [];
  });
}
function Check({
  checked,
  partial,
  label,
  onChange,
}: {
  checked: boolean;
  partial: boolean;
  label: string;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = partial;
  }, [partial]);
  return (
    <label>
      <input ref={ref} type="checkbox" checked={checked} onChange={onChange} />
      <span>{label}</span>
    </label>
  );
}
export function CategoryTreeFilter({
  value,
  onChange,
  label = "Категория",
  options = [],
  kind = "legacy",
  providedNodes,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  label?: string;
  options?: string[];
  kind?: "legacy" | "section" | "id";
  providedNodes?: CategoryNode[];
}) {
  const [open, setOpen] = useState(false),
    [requested, setRequested] = useState(false),
    [retry, setRetry] = useState(0),
    [search, setSearch] = useState(""),
    [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [offset, setOffset] = useState(0);
  const root = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null),
    input = useRef<HTMLInputElement>(null),
    id = useId();
  const { data, loading, error } = useApi<{
    nodes: CategoryNode[];
    warning?: string | null;
  }>(
    requested && !providedNodes
      ? `/reports/product-sales/catalog/category-tree?retry=${retry}`
      : "",
  );
  const nodes = useMemo(() => {
    function adapt(node: CategoryNode): CategoryNode | null {
      if (node.value) {
        const v =
          kind === "id"
            ? node.id
            : kind === "section"
              ? node.section
              : node.value;
        return v ? { ...node, value: v } : null;
      }
      return {
        ...node,
        children: node.children
          .map(adapt)
          .filter((x): x is CategoryNode => !!x),
      };
    }
    const result = (providedNodes || data?.nodes || [])
      .map(adapt)
      .filter((x): x is CategoryNode => !!x);
    const known = new Set(result.flatMap(values));
    const extra = [
      ...new Set([
        ...(kind === "legacy"
          ? ["Без категории", "Не найдено в CatalogVR"]
          : []),
        ...options,
        ...value,
      ]),
    ].filter((x) => !known.has(x));
    if (extra.length)
      result.push({
        id: "report-options",
        name: "Другие значения отчёта",
        children: extra.map((x) => ({
          id: "report:" + x,
          name: x,
          value: x,
          children: [],
        })),
      });
    return result;
  }, [data, options, value, kind, providedNodes]);
  const byId = useMemo(() => {
    const index = new Map<string, CategoryNode>();
    function walk(items: CategoryNode[]) {
      for (const node of items) {
        index.set(node.id, node);
        walk(node.children);
      }
    }
    walk(nodes);
    return index;
  }, [nodes]);
  const visible = useMemo(
    () => searchCategories(nodes, search),
    [nodes, search],
  );
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const outside = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const left = root.current?.getBoundingClientRect().left || 0;
      const width = Math.min(380, window.innerWidth - 34);
      setOffset(
        Math.max(17 - left, Math.min(0, window.innerWidth - 17 - left - width)),
      );
    };
    position();
    window.addEventListener("resize", position);
    return () => window.removeEventListener("resize", position);
  }, [open]);
  function toggle(node: CategoryNode) {
    const keys = values(node),
      all = keys.every((k) => value.includes(k));
    onChange(
      all
        ? value.filter((k) => !keys.includes(k))
        : [...new Set([...value, ...keys])],
    );
  }
  function render(node: CategoryNode, depth = 0): React.ReactNode {
    const original = byId.get(node.id) || node,
      keys = values(original),
      count = keys.filter((k) => value.includes(k)).length;
    const parent = !node.value,
      isOpen = !!search.trim() || expanded.has(node.id);
    return (
      <div key={node.id} className="tree-node">
        <div className="tree-row" style={{ paddingLeft: depth * 18 }}>
          {parent ? (
            <button
              className="tree-toggle"
              type="button"
              aria-label={`${isOpen ? "Свернуть" : "Развернуть"}: ${node.name}`}
              aria-expanded={isOpen}
              onClick={() =>
                setExpanded((old) => {
                  const next = new Set(old);
                  next.has(node.id) ? next.delete(node.id) : next.add(node.id);
                  return next;
                })
              }
            >
              {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            </button>
          ) : (
            <span className="tree-spacer" />
          )}
          <Check
            label={node.name}
            checked={!!keys.length && count === keys.length}
            partial={count > 0 && count < keys.length}
            onChange={() => toggle(original)}
          />
        </div>
        {parent && isOpen && node.children.map((x) => render(x, depth + 1))}
      </div>
    );
  }
  return (
    <div
      className="catalog-multiselect category-tree-filter"
      ref={root}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="multi-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setRequested(true);
          setOpen(!open);
        }}
      >
        <span>
          {value.length
            ? `${label}: выбрано ${value.length}`
            : `Все ${label === "Подкатегория" ? "подкатегории" : label === "Раздел" ? "разделы" : "категории"}`}
        </span>
        <ChevronDown size={16} />
      </button>
      {open && (
        <div
          id={id}
          className="catalog-dropdown"
          style={{ left: offset }}
          role="group"
          aria-label={`Дерево: ${label}`}
        >
          <div className="catalog-search">
            <Search size={16} />
            <input
              ref={input}
              aria-label={`Поиск: ${label}`}
              placeholder="Найти категорию или подкатегорию…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {loading && <p role="status">Загрузка дерева категорий…</p>}
          {error && (
            <div role="alert">
              Не удалось загрузить дерево категорий.{" "}
              <button type="button" onClick={() => setRetry((x) => x + 1)}>
                Повторить
              </button>
            </div>
          )}
          {data?.warning && (
            <p className="hint" role="status">
              {data.warning}
            </p>
          )}
          <div className="tree-list">
            {visible.map((n) => render(n))}
            {!loading && !visible.length && (
              <p role="status">Категории не найдены</p>
            )}
          </div>
          <div className="catalog-actions">
            <button
              type="button"
              disabled={!nodes.length}
              onClick={() => onChange([...new Set(nodes.flatMap(values))])}
            >
              Выбрать всё
            </button>
            <button type="button" onClick={() => onChange([])}>
              Сбросить
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
