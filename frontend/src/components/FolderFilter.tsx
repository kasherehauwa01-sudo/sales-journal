import { useMemo, useState } from 'react';
import { useApi } from '../hooks/useApi';
import './FolderFilter.css';

type Folder = { name: string; path: string; children: Map<string, Folder> };
export function buildFolderTree(paths: string[]) {
  const roots = new Map<string, Folder>();
  for (const path of paths) {
    let level = roots, prefix = '';
    for (const part of path.split('|')) {
      prefix = prefix ? `${prefix}|${part}` : part;
      let folder = level.get(part);
      if (!folder) {
        folder = { name: part.trim(), path: prefix, children: new Map() };
        level.set(part, folder);
      }
      level = folder.children;
    }
  }
  return roots;
}
function searchTree(tree: Map<string, Folder>, term: string): Map<string, Folder> {
  if (!term) return tree;
  const result = new Map<string, Folder>();
  for (const [key, folder] of tree) {
    if (folder.name.toLocaleLowerCase('ru-RU').includes(term)) result.set(key, folder);
    else {
      const children = searchTree(folder.children, term);
      if (children.size) result.set(key, { ...folder, children });
    }
  }
  return result;
}
export function FolderFilter({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { data, loading, error } = useApi<string[]>(open ? '/reports/product-sales/catalog/folders' : '');
  const tree = useMemo(() => buildFolderTree(data || []), [data]);
  const term = search.trim().toLocaleLowerCase('ru-RU');
  const visible = useMemo(() => searchTree(tree, term), [tree, term]);
  const toggle = (path: string) => onChange(value.includes(path) ? value.filter(item => item !== path) : [...value, path]);
  function nodes(folders: Map<string, Folder>) {
    return [...folders.values()].map(folder => {
      const isOpen = expanded[folder.path] ?? (Boolean(term) || value.some(path => path.startsWith(folder.path + '|')));
      return <li key={folder.path}>
        <div className="folder-filter-row">
          {folder.children.size > 0 ? <button type="button" aria-label={`${isOpen ? 'Свернуть' : 'Развернуть'}: ${folder.name}`} aria-expanded={isOpen}
            onClick={() => setExpanded(current => ({ ...current, [folder.path]: !isOpen }))}>{isOpen ? '▾' : '▸'}</button> : <span className="folder-filter-spacer" />}
          <label><input type="checkbox" checked={value.includes(folder.path)} onChange={() => toggle(folder.path)} />{folder.name}</label>
        </div>
        {isOpen && folder.children.size > 0 && <ul>{nodes(folder.children)}</ul>}
      </li>;
    });
  }
  return <div className="folder-filter" data-pdf-hidden>
    <span>Поиск по папке</span>
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      {value.length ? `Выбрано папок: ${value.length}` : 'Все папки'} {open ? '▴' : '▾'}
    </button>
    {open && <div className="folder-filter-panel" onKeyDown={event => { if (event.key === 'Escape') setOpen(false); }}>
      <input aria-label="Найти папку" placeholder="Найти папку" value={search} onChange={event => { setSearch(event.target.value); setExpanded({}); }} />
      <small>Выбранная папка включает вложенные папки.</small>
      {value.length > 0 && <><button type="button" onClick={() => onChange([])}>Сбросить папки</button>
        <div className="folder-filter-selected">{value.map(path => <button type="button" key={path} onClick={() => toggle(path)} aria-label={`Убрать папку ${path.split('|').join(' / ')}`}>{path.split('|').join(' / ')} ×</button>)}</div></>}
      {loading ? <p role="status">Загрузка папок…</p> : error ? <p role="alert">Не удалось загрузить папки: {error}</p> : <ul className="folder-filter-tree">{nodes(visible)}{!visible.size && <li role="status">Папки не найдены</li>}</ul>}
    </div>}
  </div>;
}
