"""
tracker.py — your Excel sheet of every internship found and applied to.

Uses openpyxl, a Python library that reads/writes .xlsx files directly
(Excel doesn't even need to be installed). One row per job. The ID column
stops duplicates; the Status column has a dropdown you can also edit by hand.

Tip: close the file in Excel before running the agent — Windows locks open
files and the save will fail.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from agent.models import Listing

COLUMNS = ["ID", "Company", "Title", "Location", "Source", "Link", "Date Found",
           "Fit Score", "Fit Notes", "Status", "Date Applied", "Notes"]
WIDTHS = [10, 22, 42, 22, 14, 14, 12, 9, 50, 12, 12, 30]
STATUSES = ["Found", "Interested", "Pre-filled", "Applied", "Interview", "Offer", "Rejected", "Skipped"]


class TrackerLockedError(RuntimeError):
    pass


class Tracker:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists():
            self.wb = load_workbook(self.path)
            self.ws = self.wb.active
        else:
            self._create()
        # Map "Column Name" -> column number, read from the header row.
        self.col = {c.value: c.column for c in self.ws[1] if c.value}

    def _create(self):
        self.wb = Workbook()
        self.ws = self.wb.active
        self.ws.title = "Internships"
        self.ws.append(COLUMNS)
        header_fill = PatternFill("solid", fgColor="1F2937")
        for i, cell in enumerate(self.ws[1]):
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            self.ws.column_dimensions[cell.column_letter].width = WIDTHS[i]
        self.ws.freeze_panes = "A2"   # header stays visible while scrolling
        self.ws.auto_filter.ref = f"A1:{self.ws.cell(1, len(COLUMNS)).column_letter}1"
        dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
        self.ws.add_data_validation(dv)
        status_letter = self.ws.cell(1, COLUMNS.index("Status") + 1).column_letter
        dv.add(f"{status_letter}2:{status_letter}5000")

    # ------------------------------------------------------------- reading
    def _row_dict(self, row_num: int) -> dict:
        return {name: self.ws.cell(row_num, c).value for name, c in self.col.items()} | {"_row": row_num}

    def rows(self, status: str | None = None) -> list[dict]:
        out = []
        for r in range(2, self.ws.max_row + 1):
            d = self._row_dict(r)
            if d.get("ID") and (status is None or (d.get("Status") or "").lower() == status.lower()):
                out.append(d)
        return out

    def ids(self) -> set[str]:
        return {r["ID"] for r in self.rows()}

    def find(self, job_id: str) -> dict | None:
        return next((r for r in self.rows() if r["ID"] == job_id), None)

    # ------------------------------------------------------------- writing
    def add(self, listings: list[Listing]) -> list[Listing]:
        """Append listings we haven't seen before. Returns the ones actually added."""
        known, added = self.ids(), []
        for l in listings:
            if l.id in known:
                continue
            known.add(l.id)
            self.ws.append([l.id, l.company, l.title, l.location, l.source, None,
                            l.date_found, None, None, "Found", None, None])
            link = self.ws.cell(self.ws.max_row, self.col["Link"])
            link.value, link.hyperlink, link.style = "Open", l.url, "Hyperlink"
            added.append(l)
        return added

    def update(self, job_id: str, **fields) -> None:
        """e.g. tracker.update("a1b2c3d4", Status="Applied", **{"Date Applied": "2026-11-06"})"""
        row = self.find(job_id)
        if not row:
            raise KeyError(f"No job with ID {job_id}")
        for name, value in fields.items():
            self.ws.cell(row["_row"], self.col[name]).value = value

    def mark(self, job_id: str, status: str) -> None:
        match = next((s for s in STATUSES if s.lower() == status.lower()), None)
        if not match:
            raise ValueError(f"Status must be one of: {', '.join(STATUSES)}")
        fields = {"Status": match}
        if match == "Applied":
            fields["Date Applied"] = date.today().isoformat()
        self.update(job_id, **fields)

    def link(self, job_id: str) -> str | None:
        row = self.find(job_id)
        if not row:
            return None
        cell = self.ws.cell(row["_row"], self.col["Link"])
        return cell.hyperlink.target if cell.hyperlink else cell.value

    def save(self) -> None:
        try:
            self.wb.save(self.path)
        except PermissionError as e:
            raise TrackerLockedError(f"Can't save {self.path} — close it in Excel and try again.") from e
