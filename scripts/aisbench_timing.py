"""Read observed request timing from pinned AISBench reports using only the stdlib."""
import ast
from contextlib import ExitStack, closing
import json
import math
from pathlib import Path
import sqlite3
import struct


def _timestamp_array(blob):
    """Decode only the 1-D float64 NPY v1 format AISBench writes (never pickle)."""
    if not isinstance(blob, bytes) or len(blob) < 10 or blob[:8] != b'\x93NUMPY\x01\x00':
        raise ValueError('Unsupported AISBench timestamp array format')
    header_size = struct.unpack_from('<H', blob, 8)[0]
    data_offset = 10 + header_size
    if not 0 < header_size <= 10000 or data_offset > len(blob):
        raise ValueError('Invalid AISBench timestamp array header')
    try:
        header = ast.literal_eval(blob[10:data_offset].decode('ascii').strip())
    except (ValueError, SyntaxError, UnicodeError) as exc:
        raise ValueError('Invalid AISBench timestamp array header') from exc
    if not isinstance(header, dict) or header.get('descr') not in ('<f8', '>f8'):
        raise ValueError('AISBench timestamps must be float64, not objects or other types')
    shape = header.get('shape')
    if (header.get('fortran_order') is not False or not isinstance(shape, tuple)
            or len(shape) != 1 or type(shape[0]) is not int or shape[0] < 2
            or len(blob) - data_offset != shape[0] * 8):
        raise ValueError('Invalid AISBench timestamp array shape or size')
    return [value[0] for value in struct.iter_unpack(header['descr'][0] + 'd', blob[data_offset:])]


def measured_peak_concurrency(details_path, expected_count, *, conversations=None):
    """Peak client-observed overlap of [first timestamp, last timestamp) intervals.

    AISBench's summary Max Concurrency is the configured batch_size, not evidence.
    Use the paired JSONL and its SQLite timestamp arrays; missing or malformed
    evidence is an invalid trial. Inline lists are accepted for exported reports.
    """
    events, request_ids, references = [], set(), set()
    turn_counts = {}
    databases = {}
    db_dir = details_path.parent.resolve() / 'db_data'
    try:
        with ExitStack() as stack:
            details = stack.enter_context(details_path.open())
            for line_number, line in enumerate(details, 1):
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError(f'Invalid request detail at line {line_number}')
                request_id = item.get('id')
                if type(request_id) is not int or request_id < 0 or item.get('success') is not True:
                    raise ValueError(f'Failed or invalid request detail at line {line_number}')
                if conversations is not None:
                    turn = item.get('turn_id')
                    if type(turn) is not int or turn < 0:
                        raise ValueError(f'Invalid ShareGPT turn at line {line_number}')
                    turn_counts[request_id] = turn_counts.get(request_id, 0) + 1
                    request_id = (request_id, turn)
                if request_id in request_ids:
                    raise ValueError(f'Duplicate request detail at line {line_number}')
                request_ids.add(request_id)
                if len(request_ids) > expected_count:
                    raise ValueError('Request detail count does not match the report')
                points = item.get('time_points')
                if isinstance(points, dict):
                    reference, name = points.get('__db_ref__'), item.get('db_name')
                    if (type(reference) is not int or reference < 1 or not isinstance(name, str)
                            or not name or Path(name).name != name or Path(name).suffix != '.db'):
                        raise ValueError(f'Invalid timestamp database reference at line {line_number}')
                    db_path = (db_dir / name).resolve()
                    if db_path.parent != db_dir:
                        raise ValueError('Timestamp database must stay inside the report db_data directory')
                    identity = (db_path, reference)
                    if identity in references:
                        raise ValueError('Duplicate timestamp database reference')
                    references.add(identity)
                    if db_path not in databases:
                        databases[db_path] = stack.enter_context(closing(sqlite3.connect(
                            db_path.as_uri() + '?mode=ro', uri=True)))
                    row = databases[db_path].execute(
                        'SELECT arr_blob FROM numpy_store WHERE id = ?', (reference,)).fetchone()
                    if row is None:
                        raise ValueError(f'Missing timestamp database row at line {line_number}')
                    points = _timestamp_array(row[0])
                if (not isinstance(points, list) or len(points) < 2
                        or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in points)
                        or any(end < start for start, end in zip(points, points[1:]))
                        or points[-1] <= points[0]):
                    raise ValueError(f'Invalid request timestamps at line {line_number}')
                events.extend(((points[0], 1), (points[-1], -1)))
    except (OSError, sqlite3.Error, UnicodeError) as exc:
        raise ValueError(f'Cannot read request timing evidence: {exc}') from exc
    if len(request_ids) != expected_count or not events:
        raise ValueError('Request detail count does not match the report')
    if conversations is not None and (len(turn_counts) != conversations or any(
            turn >= turn_counts[conversation] for conversation, turn in request_ids)):
        raise ValueError('ShareGPT conversation count or turn sequence differs from the workload')
    active = peak = 0
    # End (-1) precedes start (+1) at a shared timestamp: touching is not overlap.
    for _, change in sorted(events):
        active += change
        peak = max(peak, active)
    return peak
