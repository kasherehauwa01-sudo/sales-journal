"""Bounded preamble extraction for the verified 1C HTML template only."""
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
import re

from app.importer.parser import iter_html_rows, identify_column, REQUIRED

METHOD = "DECLARED_REPORT_PERIOD"
INVALID = "INVALID_REPORT_PERIOD"
_PERIOD = re.compile(r"за\s+период\s+с\s+(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))\s+по\s+"
                     r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))\s*", re.IGNORECASE)


@dataclass(frozen=True)
class DeclaredPeriod:
    start: date | None = None
    end: date | None = None
    method: str | None = None


def period_from_preamble(rows) -> DeclaredPeriod:
    declarations = set()
    for row in rows:
        text = " ".join(str(v or "") for v in row).replace("\xa0", " ").strip()
        if re.search(r"за\s+период", text, re.IGNORECASE):
            match = _PERIOD.fullmatch(text)
            if not match:
                return DeclaredPeriod(method=INVALID)
            try:
                # Explicit 20xx rule for the known export's two-digit years.
                values = [v if len(v) == 10 else v[:6] + "20" + v[6:] for v in match.groups()]
                start, end = [datetime.strptime(v, "%d.%m.%Y").date() for v in values]
                if start > end:
                    raise ValueError("Reversed period")
            except ValueError:
                return DeclaredPeriod(method=INVALID)
            declarations.add((start, end))
    if len(declarations) > 1:
        return DeclaredPeriod(method=INVALID)
    if declarations:
        start, end = declarations.pop()
        return DeclaredPeriod(start, end, METHOD)
    return DeclaredPeriod()


def extract_declared_period(path: Path) -> DeclaredPeriod:
    with path.open("rb") as source:
        signature = source.read(8)
    if path.suffix.lower() not in {".html", ".htm"} or signature.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        return DeclaredPeriod()  # Real XLS/XLSX preambles have no verified contract.
    rows = iter_html_rows(path)
    preamble = []
    try:
        for row in rows:
            if REQUIRED.issubset({identify_column(v) for v in row}):
                return period_from_preamble(preamble)
            preamble.append(row)
            if len(preamble) >= 64:
                break
    finally:
        rows.close()
    return DeclaredPeriod(method=INVALID if any(re.search(r"за\s+период", str(r), re.I) for r in preamble) else None)
