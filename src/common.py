"""Helpers shared by the scraping and dataset scripts."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def norm(name) -> str:
    """Lower-case, strip accents and punctuation so 'St. Geo Illa' matches 'st geo illa'."""
    if name is None or (isinstance(name, float) and math.isnan(name)):
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class AliasResolver:
    """Resolve a raw team or venue name to a reference row, respecting season ranges.

    The same alias can point to different entities in different eras (for example
    'Gold Coast' is the Chargers in 1995 to 1998 and the Titans from 2007), so each
    reference row carries first_season and last_season.
    """

    def __init__(self, ref: pd.DataFrame, id_col: str):
        self.id_col = id_col
        self.index: dict[str, list[dict]] = {}
        for row in ref.to_dict("records"):
            for alias in str(row["aliases"]).split("|"):
                self.index.setdefault(norm(alias), []).append(row)

    def resolve(self, name, season: int) -> dict | None:
        cands = [r for r in self.index.get(norm(name), [])
                 if int(r["first_season"]) <= season <= int(r["last_season"])]
        ids = {r[self.id_col] for r in cands}
        if len(ids) > 1:
            raise ValueError(f"Ambiguous alias {name!r} in {season}: {sorted(ids)}")
        return cands[0] if cands else None


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km; accepts scalars, arrays or Series (NaN propagates)."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def local_to_utc(local_naive: datetime, tz: str) -> datetime:
    """Attach an IANA zone to a naive local time and convert to UTC (daylight saving applied by date)."""
    return local_naive.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def utc_offset_hours(tz: str, instant_utc: datetime) -> float:
    """UTC offset of a zone at a given instant, in hours (e.g. Perth 8.0, Sydney 10.0 or 11.0)."""
    return instant_utc.astimezone(ZoneInfo(tz)).utcoffset().total_seconds() / 3600


def clock_hour(tz: str, instant_utc: datetime) -> float:
    """Decimal clock hour of an instant in the given zone (19.83 = 7:50pm)."""
    t = instant_utc.astimezone(ZoneInfo(tz))
    return t.hour + t.minute / 60
