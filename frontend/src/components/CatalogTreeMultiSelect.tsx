import { CategoryTreeFilter } from "./CategoryTreeFilter";
// Catalog's integration search accepts exact section names, never display IDs.
export function CatalogTreeMultiSelect({
  value,
  onChange,
  label = "Раздел",
}: {
  value: string[];
  onChange: (value: string[]) => void;
  label?: string;
}) {
  return (
    <CategoryTreeFilter
      value={value}
      onChange={onChange}
      label={label}
      kind="section"
    />
  );
}
