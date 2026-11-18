"""Leitura da base particionada e busca por proximidade."""

from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path

from cepdb.aggregate import GRID_DEG, haversine_km, normalize_cep

_REQUIRED = (
    "cep",
    "logradouro",
    "complemento",
    "bairro",
    "cidade",
    "uf",
    "codigo_ibge",
    "lat",
    "lng",
    "precisao",
    "fonte",
    "origem",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("CEPDB_DATA")
    if override:
        return Path(override)
    bundled = repo_root() / "data"
    if (bundled / "meta.json").is_file():
        return bundled
    return Path.cwd() / "data"


class CepNaoEncontrado(LookupError):
    pass


class BaseAusente(FileNotFoundError):
    pass


class Database:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else data_dir()
        meta_path = self.root / "meta.json"
        if not meta_path.is_file():
            raise BaseAusente(
                f"Base não encontrada em {self.root}. Gere os dados com: python -m cepdb.build"
            )
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.grid_deg = int(self.meta.get("grade_graus", GRID_DEG))
        self._cep_files: dict[str, dict] = {}
        self._cells: dict[str, list] = {}

    def lookup(self, cep: str) -> dict | None:
        normalized = normalize_cep(cep)
        if normalized is None:
            raise ValueError(f"CEP inválido: {cep!r}. Informe 8 dígitos, com ou sem hífen.")
        blob = self._load_prefix(normalized[:2])
        record = blob.get(normalized)
        return record

    def require(self, cep: str) -> dict:
        record = self.lookup(cep)
        if record is None:
            normalized = normalize_cep(cep)
            raise CepNaoEncontrado(f"CEP não encontrado: {normalized}")
        return record

    def near(self, lat: float, lng: float, radius_km: float = 5.0, limit: int = 10) -> list[dict]:
        if radius_km <= 0:
            raise ValueError("radius_km deve ser positivo.")
        if limit <= 0:
            raise ValueError("limit deve ser positivo.")
        if not math.isfinite(lat) or not math.isfinite(lng):
            raise ValueError("lat/lng precisam ser números finitos.")

        hits: list[tuple[float, str]] = []
        for name in _cells_for_radius(lat, lng, radius_km, self.grid_deg):
            for item in self._load_cell(name):
                cep, clat, clng = item[0], item[1], item[2]
                dist = haversine_km(lat, lng, clat, clng)
                if dist <= radius_km:
                    hits.append((dist, cep))
        hits.sort(key=lambda pair: (pair[0], pair[1]))
        results = []
        for dist, cep in hits[:limit]:
            record = self.require(cep)
            enriched = dict(record)
            enriched["dist_km"] = round(dist, 3)
            results.append(enriched)
        return results

    def _load_prefix(self, prefix: str) -> dict:
        cached = self._cep_files.get(prefix)
        if cached is not None:
            return cached
        path = self.root / "ceps" / f"{prefix}.json"
        if not path.is_file():
            self._cep_files[prefix] = {}
            return self._cep_files[prefix]
        data = json.loads(path.read_text(encoding="utf-8"))
        self._cep_files[prefix] = data
        return data

    def _load_cell(self, name: str) -> list:
        cached = self._cells.get(name)
        if cached is not None:
            return cached
        path = self.root / "grid" / f"{name}.json"
        if not path.is_file():
            self._cells[name] = []
            return self._cells[name]
        data = json.loads(path.read_text(encoding="utf-8"))
        self._cells[name] = data
        return data


def _cells_for_radius(lat: float, lng: float, radius_km: float, deg: int) -> list[str]:
    lat_pad = radius_km / 110.574
    cosine = math.cos(math.radians(lat))
    lng_pad = radius_km / (111.320 * max(0.2, abs(cosine)))
    lat0 = math.floor((lat - lat_pad) / deg)
    lat1 = math.floor((lat + lat_pad) / deg)
    lng0 = math.floor((lng - lng_pad) / deg)
    lng1 = math.floor((lng + lng_pad) / deg)
    names = []
    for ilat in range(lat0, lat1 + 1):
        for ilng in range(lng0, lng1 + 1):
            names.append(f"{ilat}_{ilng}")
    return names


@lru_cache(maxsize=1)
def default_db() -> Database:
    return Database()


def lookup(cep: str, root: Path | None = None) -> dict | None:
    db = Database(root) if root is not None else default_db()
    return db.lookup(cep)


def record_fields_ok(record: dict) -> bool:
    return all(key in record for key in _REQUIRED)
