import { useEffect, useState } from "react";
import { query } from "../api/client";
import { MultiSelect } from "./MultiSelect";
import { useApi } from "../hooks/useApi";

export function BrandFilter({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setTerm(search), 300);
    return () => clearTimeout(timer);
  }, [search]);
  // Use the same configured CatalogVR source as product analytics, not only
  // brands with an already matched local sales lookup.
  const { data, error, loading } = useApi<string[]>(
    `/reports/product-analytics/catalog-options/brand?${query({ search: term })}`,
  );
  return <label>Бренд
    <MultiSelect
      options={[...new Set([...value, ...(data || [])])]}
      value={value} onChange={onChange} placeholder="Все бренды"
      searchable showActions search={search} onSearch={setSearch}
      emptyText={loading ? "Загрузка брендов…" : error ? "Справочник брендов недоступен" : "Бренды не найдены"}
    />
    {error && <small role="alert">Не удалось загрузить бренды: {error}</small>}
  </label>;
}
