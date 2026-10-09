import io
import math
import pickle
import random
import zlib
from datetime import date

import pytest

from app.services import product_analytics_cache as cache


@pytest.mark.parametrize('size', [0, 8192, 65535, 65536, 262143, 262144, 262145, 1048576])
@pytest.mark.parametrize('compressible', [False, True])
def test_buffered_reader_preserves_bytes_across_input_and_output_boundaries(size, compressible):
    raw = b'A' * size if compressible else random.Random(size).randbytes(size)
    payload = bytearray(zlib.compress(raw, level=1))
    with io.BufferedReader(cache._CompressedReader(payload), buffer_size=256 * 1024) as reader:
        parts = [reader.read(17), reader.read(65537), reader.read()]
        assert b''.join(parts) == raw
        assert reader.read() == b''


def test_new_restore_matches_old_reader_with_large_fields_types_aliases_and_order():
    rng = random.Random(42)
    shared = {'date': date(2026, 9, 30), 'values': [math.nextafter(1.0, 2.0), -0.0, None]}
    rows = [{'key': f'code:{i}', 'data': rng.randbytes(300000), 'shared': shared,
             'float': math.nextafter(float(i + 1), math.inf)} for i in [9, 2, 8, 1]]
    value = (rows, date(2025, 1, 1), date(2025, 9, 30), ['Клиент'])
    packed = cache._compact(value)
    with io.BufferedReader(cache._CompressedReader(packed.payload)) as reader:
        old = pickle.Unpickler(reader).load()
    restored = cache._restore(packed)
    assert restored == old == value
    assert [row['key'] for row in restored[0]] == ['code:9', 'code:2', 'code:8', 'code:1']
    assert restored[0][0]['shared'] is restored[0][3]['shared']
    assert restored[0][0]['float'].hex() == rows[0]['float'].hex()
    assert math.copysign(1, restored[0][0]['shared']['values'][1]) == -1
    assert isinstance(restored[0][0]['shared']['date'], date)
    assert isinstance(restored[0][0]['data'], bytes)


def test_reader_reports_truncated_stream():
    payload = zlib.compress(random.Random(1).randbytes(300000))[:-20]
    with io.BufferedReader(cache._CompressedReader(payload), buffer_size=256 * 1024) as reader:
        with pytest.raises(ValueError, match='Truncated internal analytics cache'):
            reader.read()


def test_reader_reports_corrupted_stream():
    payload = bytearray(zlib.compress(b'payload'))
    payload[0] ^= 255
    with io.BufferedReader(cache._CompressedReader(payload), buffer_size=256 * 1024) as reader:
        with pytest.raises(zlib.error):
            reader.read()


def test_restore_leaves_noncompressed_values_unchanged():
    value = {'rows': [1, 2]}
    assert cache._restore(value) is value
