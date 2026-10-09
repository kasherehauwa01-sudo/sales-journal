"""Synthetic benchmark; no database/network access or production measurements."""
import gc
import json
import time
import tracemalloc
from datetime import date
from app.services.product_analytics import merge_periods
from app.services import product_analytics_cache as cache


def sample_dataset(count=27016):
    current = [{'key': f'code:{i}', 'code': str(i), 'article': f'ART-{i}', 'name': f'Товар {i}', 'revenue': float(i + 1), 'units': float(i % 7 + 1), 'checks': i % 13, 'first_sale': date(2026, 1, 1), 'last_sale': date(2026, 9, 30)} for i in range(count)]
    previous = [dict(row, revenue=row['revenue'] / 2, units=row['units'] + 1) for row in current]
    catalog = {row['key']: {'brand': f'Бренд {i % 40}', 'manufacturer': f'Завод {i % 20}', 'category': f'Категория {i % 10}', 'section': f'Раздел {i % 80}', 'material': 'Стекло', 'image_url': f'https://catalog.test/media/{i}.jpg', 'properties': [{'name': 'Цвет', 'value': 'Прозрачный'}], 'stocks': [{'warehouse': 'Основной', 'quantity': i % 17}], 'prices': [{'name': 'Розница', 'value': float(i + 100)}]} for i, row in enumerate(current)}
    value = (merge_periods(current, previous, catalog), date(2025, 1, 1), date(2025, 9, 30), [f'клиент {i}' for i in range(1000)])
    value[0].sort(key=lambda row: row['key'])
    return value


def main():
    value = sample_dataset()
    gc.collect()
    tracemalloc.start()
    components = cache.dataset_memory_components(value)
    _, profile_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    raw = sum(components.values())
    tracemalloc.start()
    start = time.perf_counter()
    compact = cache._compact(value)
    encode_ms = (time.perf_counter() - start) * 1000
    _, encode_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    packed = cache._bounded_size(compact, float('inf'))
    tracemalloc.start()
    start = time.perf_counter()
    restored = cache._restore(compact)
    decode_ms = (time.perf_counter() - start) * 1000
    _, restore_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert restored == value
    print(json.dumps({'rows': len(value[0]), 'components_bytes': components,
                      'raw_bytes': raw, 'compressed_bytes': packed,
                      'reduction_percent': (1 - packed / raw) * 100,
                      'encode_ms_with_tracing': encode_ms,
                      'restore_ms_with_tracing': decode_ms,
                      'profile_peak_additional_bytes': profile_peak,
                      'encode_peak_additional_bytes': encode_peak,
                      'restore_peak_additional_bytes': restore_peak,
                      'fits_entry_limit': packed <= cache.CACHE_MAX_ENTRY_BYTES}, indent=2))


if __name__ == '__main__':
    main()
