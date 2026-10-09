"""Local synthetic comparison; never connects to the DB or CatalogVR.

Each variant runs in its own process. Timing is measured without tracemalloc;
allocation peak is measured in a separate pass after setup, on identical data.
"""
import argparse
import base64
import gc
import io
import json
import pickle
import random
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
import zlib

from app.services import product_analytics_cache as cache
from scripts.benchmark_product_analytics_cache import sample_dataset

VARIANTS = {
    'baseline_8k': (65536, 8192),
    'buffer_64k': (65536, 65536),
    'buffer_256k': (65536, 262144),
    'buffer_1m': (65536, 1048576),
    'input_16k': (16384, 8192),
    'input_256k_buffer_256k': (262144, 262144),
    'whole_decompress': None,
}


class CountingReader(io.RawIOBase):
    """Baseline reader with configurable blocks and diagnostic counters."""
    def __init__(self, payload, input_size):
        self.payload = memoryview(payload)
        self.position = 0
        self.decompressor = zlib.decompressobj()
        self.input_size = input_size
        self.calls = 0
        self.tail_bytes = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        while not self.decompressor.eof:
            chunk = self.decompressor.unconsumed_tail
            self.tail_bytes += len(chunk)
            if not chunk:
                chunk = self.payload[self.position:self.position + self.input_size]
                self.position += len(chunk)
                if not chunk:
                    raise ValueError('Truncated internal analytics cache')
            self.calls += 1
            data = self.decompressor.decompress(chunk, len(buffer))
            if data:
                buffer[:len(data)] = data
                return len(data)
        return 0


def restore_variant(packed, name):
    parameters = VARIANTS[name]
    if parameters is None:
        return pickle.loads(zlib.decompress(packed.payload)), 0, 0
    input_size, output_size = parameters
    reader = CountingReader(packed.payload, input_size)
    with io.BufferedReader(reader, buffer_size=output_size) as stream:
        result = pickle.Unpickler(stream).load()
    return result, reader.calls, reader.tail_bytes


def fixture(rows):
    value = sample_dataset(rows)
    rng = random.Random(17)
    # High-entropy nested text brings the compressed payload close to the
    # reported 21.8 MiB, unlike the earlier highly repetitive 1.7 MiB fixture.
    for row in value[0]:
        row['properties'].append({'name': 'Описание', 'value': base64.b85encode(rng.randbytes(768)).decode('ascii')})
    return value


def worker(name, rows, repeats):
    value = fixture(rows)
    packed = cache._compact(value)
    assert isinstance(packed, cache.CompressedDataset)
    timings = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        restored, calls, tails = restore_variant(packed, name)
        timings.append((time.perf_counter() - start) * 1000)
        assert restored == value
        del restored
    gc.collect()
    tracemalloc.start()
    restored, calls, tails = restore_variant(packed, name)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert restored == value
    # Confirm the chosen benchmark variant is identical to production restore.
    if name == 'buffer_256k':
        del restored
        restored = cache._restore(packed)
        assert restored == value
    return {'variant': name, 'rows': rows, 'compressed_bytes': len(packed.payload),
            'timings_ms': timings, 'median_ms': statistics.median(timings),
            'peak_additional_bytes': peak,
            'process_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'decompress_calls': calls, 'unconsumed_tail_bytes': tails}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=27016)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--variant', choices=VARIANTS)
    args = parser.parse_args()
    if args.rows < 0 or args.repeats < 1:
        parser.error('rows must be nonnegative; repeats must be positive')
    if args.variant:
        print(json.dumps(worker(args.variant, args.rows, args.repeats)))
        return
    results = []
    for name in VARIANTS:
        completed = subprocess.run(
            [sys.executable, __file__, '--variant', name, '--rows', str(args.rows), '--repeats', str(args.repeats)],
            check=True, capture_output=True, text=True,
        )
        results.append(json.loads(completed.stdout))
        print(f'{name} completed', file=sys.stderr, flush=True)
    print(json.dumps({'python': sys.version, 'platform': sys.platform, 'results': results}, indent=2))


if __name__ == '__main__':
    main()
