"""Generate a sample opportunities .xlsx with deliberately messy headers,
to exercise the fuzzy column mapping. Run:

    uv run python scripts/make_sample.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook  # noqa: E402

OUT = Path("data/sample_opportunities.xlsx")

HEADERS = [
    "Opportunity Title", "Sol. #", "Notice ID", "Department/Agency", "NAICS Code",
    "PSC", "Set-Aside Type", "Notice Type", "Posted On", "Response Due Date",
    "Estimated Value ($)", "Place of Performance", "Point of Contact",
    "Contact Email", "Internal Notes",
]

ROWS = [
    ["Tactical Network Modernization Services", "W15P7T-26-R-0012", "abc123",
     "U.S. Army CECOM", "541512", "D399", "WOSB", "Solicitation", "2026-06-01",
     "2026-08-15", "$22,000,000", "Aberdeen, MD", "Jane Smith",
     "jane.smith@army.mil", "Strong fit - WIN-T past perf"],
    ["Enterprise Cybersecurity & RMF Support", "FA8773-26-R-0044", "def456",
     "U.S. Air Force", "541519", "DA01", "Small Business", "Sources Sought",
     "2026-05-20", "2026-07-20", "7,500,000", "San Antonio, TX", "Bob Lee",
     "robert.lee@us.af.mil", "Lost similar in 2024 - revisit"],
    ["SATCOM Field Engineering Services", "N00039-26-R-0301", "ghi789",
     "U.S. Navy", "541330", "R425", "Full & Open", "Presolicitation",
     "2026-06-10", "2026-09-05", "$12.3M", "San Diego, CA", "Maria Gomez",
     "maria.gomez@navy.mil", "Needs cleared engineers"],
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Opportunities"
    ws.append(HEADERS)
    for r in ROWS:
        ws.append(r)
    wb.save(OUT)
    print(f"Wrote sample file: {OUT}")


if __name__ == "__main__":
    main()
