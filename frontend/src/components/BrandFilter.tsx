import { MultiSelect } from "./MultiSelect";
import { useApi } from "../hooks/useApi";
export function BrandFilter({ value, onChange }: {value: string[]; onChange: (value: string[]) => void}) {
  const { data, error, loading } = useApi<string[]>("/reports/brands");
  return <label>Бренд<MultiSelect options={[...new Set([...value, ...(data || [])])]} value={value} onChange={onChange} placeholder={loading ? "Загрузка брендов…" : "Все бренды"} searchable showActions />{error && <small role="alert">Не удалось загрузить бренды: {error}</small>}</label>;
}
