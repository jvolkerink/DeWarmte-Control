"""Observatielog met maandbestanden en retentie (draait in een executor)."""
from __future__ import annotations

import csv
import glob
import json
import os
import time


class ObsLog:
    def __init__(self, directory: str, keep: int = 12) -> None:
        self.directory, self.keep = directory, keep

    def append(self, row: dict) -> None:
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"observations_{time.strftime('%Y-%m')}.csv")
        header = list(row.keys())
        exists = os.path.exists(path)
        if exists:
            with open(path, encoding="utf-8") as fh:
                if fh.readline().strip() != ",".join(header):
                    os.replace(path, f"{path}.old-{int(time.time())}")
                    exists = False
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if not exists:
                writer.writerow(header)
                self._prune()
            writer.writerow([json.dumps(v) if isinstance(v, (list, dict)) else v for v in row.values()])

    def _prune(self) -> None:
        files = sorted(glob.glob(os.path.join(self.directory, "observations_*.csv*")))
        for old in files[: max(0, len(files) - self.keep)]:
            try:
                os.remove(old)
            except OSError:
                pass

    def service_log(self, item: dict) -> None:
        """Elke handmatige servicewijziging in /config/dewarmte/servicelog.csv (nooit opgeschoond)."""
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, "servicelog.csv")
        cols = ["datum", "time", "register", "name", "old", "new", "ok", "error"]
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if new:
                writer.writerow(cols)
            writer.writerow([item.get(c) for c in cols])
