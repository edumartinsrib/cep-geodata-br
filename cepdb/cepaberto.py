"""Incorpora o dump postal do CEP Aberto à base já agregada do CNEFE.

O site só entrega o tar com sessão autenticada, então este módulo não baixa
nada. O tar de 2026-11-17 vai versionado em sources/cepaberto/. Um dump
mais novo pode ser passado com --tar.

O dump não tem latitude/longitude. Coordenada de CEP que já está no CNEFE
não é substituída. CEP só do CEP Aberto recebe, nesta ordem, o centroide
ponderado por n_enderecos de:

1. CEPs do CNEFE com o mesmo logradouro, o mesmo bairro e o mesmo município;
2. CEPs do CNEFE com o mesmo bairro e o mesmo município;
3. todos os CEPs do CNEFE do município.

O campo precisao registra esse método: cnefe_cep, logradouro, bairro,
municipio ou null.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import tarfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from cepdb import __version__
from cepdb.aggregate import FONTE, GRID_DEG, cell_id, fold_key, normalize_cep
from cepdb.build import _sha256, rss_mb

FONTE_ABERTO = "cepaberto"
FONTE_MISTA = "ibge-cnefe-censo2022+cepaberto"
TAR_NAME = "cepaberto_2026-11-17.tar"

_TRAILING_NUMBER = re.compile(
    r"(?:[\s,]+n[ºo°.]\s*\d+[a-zA-Z]?|[\s,]+\d+[a-zA-Z]?)$",
    re.IGNORECASE,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_tar() -> Path:
    return repo_root() / "sources" / "cepaberto" / TAR_NAME


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def clean_complemento(value: str | None) -> str | None:
    """Normaliza o complemento do dump sem inventar texto.

    O CEP Aberto prefixa o complemento com '- ' quando o campo existe
    ('- lado ímpar', '- de 612 a 1510 - lado par'). Esse prefixo é removido.
    """
    if value is None:
        return None
    text = " ".join(value.split())
    if not text or text in {"-", "–", "—"}:
        return None
    if text.startswith("- "):
        text = text[2:].strip()
    elif text.startswith("-") and len(text) > 1:
        text = text[1:].strip()
    if not text or text in {"-", "–", "—"}:
        return None
    return text


def street_match_key(value: str | None) -> str | None:
    """Chave de comparação de logradouro.

    Um número no fim ('Avenida Paulista 52', 'Praça da Sé 108') não entra
    na chave: o dump usa isso para CEP de um prédio, e o CNEFE nomeia a rua.
    O texto gravado no registro continua o original.
    """
    if not value:
        return None
    text = " ".join(value.split())
    previous = None
    while text and text != previous:
        previous = text
        text = _TRAILING_NUMBER.sub("", text).strip(" ,")
    if not text:
        return None
    return fold_key(text)


def choose_text(cnefe_value: str | None, aberto_value: str | None) -> tuple[str | None, str | None, str | None]:
    """Escolhe o texto exibido, a origem e o valor do CNEFE se ficou de fora.

    Textos iguais depois de ignorar acento e caixa contam como o mesmo nome.
    A grafia do CEP Aberto é a exibida nesse caso. Se divergem, o CEP Aberto
    é o exibido e o texto do CNEFE volta no terceiro item para não se perder.
    """
    cnefe = " ".join(cnefe_value.split()) if cnefe_value and cnefe_value.strip() else None
    aberto = " ".join(aberto_value.split()) if aberto_value and aberto_value.strip() else None
    if cnefe is None and aberto is None:
        return None, None, None
    if cnefe is None:
        return aberto, "cepaberto", None
    if aberto is None:
        return cnefe, "cnefe", None
    if fold_key(cnefe) == fold_key(aberto):
        return aberto, "cnefe+cepaberto", None
    return aberto, "cepaberto", cnefe


def choose_cidade(cnefe_value: str | None, aberto_value: str | None) -> tuple[str | None, str | None, str | None]:
    """Município do IBGE não é trocado por localidade do CEP Aberto.

    O cadastro do CEP Aberto mistura município e distrito (10,6 mil nomes).
    Quando os nomes não batem, a cidade oficial permanece e o nome do dump
    volta no terceiro item. Campo vazio do CNEFE é preenchido pelo dump.
    """
    cnefe = " ".join(cnefe_value.split()) if cnefe_value and cnefe_value.strip() else None
    aberto = " ".join(aberto_value.split()) if aberto_value and aberto_value.strip() else None
    if cnefe is None and aberto is None:
        return None, None, None
    if cnefe is None:
        return aberto, "cepaberto", None
    if aberto is None:
        return cnefe, "cnefe", None
    if fold_key(cnefe) == fold_key(aberto):
        return cnefe, "cnefe+cepaberto", None
    return cnefe, "cnefe", aberto


def _parent_name(city_name: str) -> str | None:
    if "(" not in city_name or not city_name.endswith(")"):
        return None
    parent = city_name[city_name.rfind("(") + 1 : -1].strip()
    return parent or None


class _Bucket:
    __slots__ = ("sum_lat", "sum_lng", "weight", "ceps")

    def __init__(self) -> None:
        self.sum_lat = 0.0
        self.sum_lng = 0.0
        self.weight = 0
        self.ceps = 0

    def add(self, lat: float, lng: float, weight: int) -> None:
        self.sum_lat += lat * weight
        self.sum_lng += lng * weight
        self.weight += weight
        self.ceps += 1

    def centroid(self) -> tuple[float, float] | None:
        if self.weight <= 0:
            return None
        return round(self.sum_lat / self.weight, 6), round(self.sum_lng / self.weight, 6)


def _add_bucket(index: dict, key, lat: float, lng: float, weight: int) -> None:
    bucket = index.get(key)
    if bucket is None:
        bucket = _Bucket()
        index[key] = bucket
    bucket.add(lat, lng, weight)


def load_cnefe(data_dir: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    cep_dir = data_dir / "ceps"
    files = sorted(cep_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"Nenhuma partição CNEFE em {cep_dir}. Rode python -m cepdb.build antes.")
    for path in files:
        blob = json.loads(path.read_text(encoding="utf-8"))
        records.update(blob)
        _log(f"leu {path.name}: {len(blob):,} | acumulado {len(records):,} | RSS {rss_mb():.0f} MiB")
    return records


def index_cnefe(records: dict[str, dict]) -> tuple[dict, dict, dict, dict]:
    """Índices para herdar coordenada e a tabela município -> código IBGE."""
    streets: dict[tuple[str, str, str], _Bucket] = {}
    bairros: dict[tuple[str, str], _Bucket] = {}
    municipios: dict[str, _Bucket] = {}
    by_name: dict[tuple[str, str], str] = {}
    ambiguous: set[tuple[str, str]] = set()
    for record in records.values():
        code = record.get("codigo_ibge")
        uf = record.get("uf")
        cidade = record.get("cidade")
        if code and uf and cidade:
            key = (uf, fold_key(cidade))
            previous = by_name.get(key)
            if previous is None:
                by_name[key] = code
            elif previous != code:
                ambiguous.add(key)
        lat = record.get("lat")
        lng = record.get("lng")
        if lat is None or lng is None or not code:
            continue
        weight = record.get("n_enderecos") or 1
        try:
            weight = int(weight)
        except (TypeError, ValueError):
            weight = 1
        if weight < 1:
            weight = 1
        _add_bucket(municipios, code, lat, lng, weight)
        bairro_key = fold_key(record["bairro"]) if record.get("bairro") else None
        street_key = street_match_key(record.get("logradouro"))
        if bairro_key:
            _add_bucket(bairros, (code, bairro_key), lat, lng, weight)
            if street_key:
                _add_bucket(streets, (code, street_key, bairro_key), lat, lng, weight)
    for key in ambiguous:
        by_name.pop(key, None)
    _log(
        f"índice: {len(streets):,} ruas, {len(bairros):,} bairros, "
        f"{len(municipios):,} municípios | RSS {rss_mb():.0f} MiB"
    )
    return streets, bairros, municipios, by_name


def match_municipio(city_name: str, uf: str, by_name: dict[tuple[str, str], str]) -> tuple[str | None, str]:
    exact = by_name.get((uf, fold_key(city_name)))
    if exact:
        return exact, "exato"
    parent = _parent_name(city_name)
    if parent:
        code = by_name.get((uf, fold_key(parent)))
        if code:
            return code, "parentese"
    return None, "nenhum"


def derive_coordinate(
    codigo: str | None,
    logradouro: str | None,
    bairro: str | None,
    streets: dict,
    bairros: dict,
    municipios: dict,
) -> tuple[float | None, float | None, str | None]:
    if codigo and logradouro and bairro:
        street_key = street_match_key(logradouro)
        bairro_key = fold_key(bairro)
        if street_key and bairro_key:
            bucket = streets.get((codigo, street_key, bairro_key))
            if bucket is not None:
                lat, lng = bucket.centroid()
                return lat, lng, "logradouro"
    if codigo and bairro:
        bairro_key = fold_key(bairro)
        bucket = bairros.get((codigo, bairro_key))
        if bucket is not None:
            lat, lng = bucket.centroid()
            return lat, lng, "bairro"
    if codigo:
        bucket = municipios.get(codigo)
        if bucket is not None:
            lat, lng = bucket.centroid()
            return lat, lng, "municipio"
    return None, None, None


def _origem_coordenada(precisao: str | None) -> str | None:
    if precisao == "cnefe_cep":
        return "cnefe"
    if precisao == "logradouro":
        return "cnefe_logradouro"
    if precisao == "bairro":
        return "cnefe_bairro"
    if precisao == "municipio":
        return "cnefe_municipio"
    return None


def stamp_cnefe(record: dict) -> dict:
    lat = record.get("lat")
    precisao = "cnefe_cep" if lat is not None else None
    record["complemento"] = record.get("complemento")
    record["precisao"] = precisao
    record["fonte"] = FONTE
    record["origem"] = {
        "logradouro": "cnefe" if record.get("logradouro") else None,
        "complemento": None,
        "bairro": "cnefe" if record.get("bairro") else None,
        "cidade": "cnefe" if record.get("cidade") else None,
        "coordenada": "cnefe" if lat is not None else None,
    }
    record.pop("divergencias", None)
    return record


def apply_cepaberto_to_cnefe(
    record: dict,
    aberto: dict,
    streets: dict,
    bairros: dict,
    municipios: dict,
) -> dict:
    logradouro, origem_log, div_log = choose_text(record.get("logradouro"), aberto.get("logradouro"))
    bairro, origem_bairro, div_bairro = choose_text(record.get("bairro"), aberto.get("bairro"))
    complemento = clean_complemento(aberto.get("complemento"))
    cidade, origem_cidade, div_cidade = choose_cidade(record.get("cidade"), aberto.get("cidade"))
    divergencias = {}
    if div_log:
        divergencias["logradouro"] = div_log
    if div_bairro:
        divergencias["bairro"] = div_bairro
    if div_cidade:
        divergencias["cidade"] = div_cidade
    if record.get("uf") and aberto.get("uf") and record["uf"] != aberto["uf"]:
        divergencias["uf"] = aberto["uf"]
    if record.get("lat") is None:
        lat, lng, precisao = derive_coordinate(
            record.get("codigo_ibge"), aberto.get("logradouro"), aberto.get("bairro"), streets, bairros, municipios
        )
        record["lat"] = lat
        record["lng"] = lng
        origem_coordenada = _origem_coordenada(precisao)
    else:
        precisao = "cnefe_cep"
        origem_coordenada = "cnefe"
    record["logradouro"] = logradouro
    record["bairro"] = bairro
    record["complemento"] = complemento
    record["cidade"] = cidade
    record["fonte"] = FONTE_MISTA
    record["precisao"] = precisao
    record["origem"] = {
        "logradouro": origem_log,
        "complemento": "cepaberto" if complemento else None,
        "bairro": origem_bairro,
        "cidade": origem_cidade,
        "coordenada": origem_coordenada,
    }
    if divergencias:
        record["divergencias"] = divergencias
    else:
        record.pop("divergencias", None)
    return record


def new_from_cepaberto(
    cep: str,
    aberto: dict,
    codigo: str | None,
    cidade_exibida: str | None,
    origem_cidade: str | None,
    streets: dict,
    bairros: dict,
    municipios: dict,
) -> dict:
    lat, lng, precisao = derive_coordinate(
        codigo, aberto.get("logradouro"), aberto.get("bairro"), streets, bairros, municipios
    )
    complemento = clean_complemento(aberto.get("complemento"))
    return {
        "cep": cep,
        "logradouro": aberto.get("logradouro"),
        "complemento": complemento,
        "bairro": aberto.get("bairro"),
        "cidade": cidade_exibida,
        "uf": aberto.get("uf"),
        "codigo_ibge": codigo,
        "lat": lat,
        "lng": lng,
        "precisao": precisao,
        "fonte": FONTE_ABERTO,
        "origem": {
            "logradouro": "cepaberto" if aberto.get("logradouro") else None,
            "complemento": "cepaberto" if complemento else None,
            "bairro": "cepaberto" if aberto.get("bairro") else None,
            "cidade": origem_cidade,
            "coordenada": _origem_coordenada(precisao),
        },
        "n_enderecos": None,
        "nv_geo": None,
        "n_logradouros": None,
    }


def _ordered(record: dict) -> dict:
    ordered = {
        "cep": record.get("cep"),
        "logradouro": record.get("logradouro"),
        "complemento": record.get("complemento"),
        "bairro": record.get("bairro"),
        "cidade": record.get("cidade"),
        "uf": record.get("uf"),
        "codigo_ibge": record.get("codigo_ibge"),
        "lat": record.get("lat"),
        "lng": record.get("lng"),
        "precisao": record.get("precisao"),
        "fonte": record.get("fonte"),
        "origem": record.get("origem"),
    }
    if record.get("divergencias"):
        ordered["divergencias"] = record["divergencias"]
    ordered["n_enderecos"] = record.get("n_enderecos")
    ordered["nv_geo"] = record.get("nv_geo")
    ordered["n_logradouros"] = record.get("n_logradouros")
    return ordered


def _csv_from_zip(archive: zipfile.ZipFile) -> csv.reader:
    names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(names) != 1:
        raise RuntimeError(f"ZIP sem um CSV único: {archive.namelist()}")
    text = io.TextIOWrapper(archive.open(names[0]), encoding="utf-8-sig", newline="")
    return csv.reader(text)


def iter_cepaberto(tar_path: Path):
    """Gera (cep, dict) a partir do tar de zips do CEP Aberto."""
    with tarfile.open(tar_path, "r") as tar:
        members = [member for member in tar.getmembers() if member.isfile() and member.name.endswith(".zip")]
        states: dict[str, str] = {}
        cities: dict[str, tuple[str, str]] = {}
        for member in members:
            base = Path(member.name).name
            if base == "estados.cepaberto.zip":
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError("estados.cepaberto.zip ilegível")
                with zipfile.ZipFile(io.BytesIO(extracted.read())) as archive:
                    for row in _csv_from_zip(archive):
                        if len(row) != 3:
                            continue
                        states[row[0]] = row[2].strip().upper()
            elif base == "cidades.cepaberto.zip":
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError("cidades.cepaberto.zip ilegível")
                with zipfile.ZipFile(io.BytesIO(extracted.read())) as archive:
                    for row in _csv_from_zip(archive):
                        if len(row) != 3:
                            continue
                        cities[row[0]] = (row[1].strip(), row[2].strip())
        if not states or not cities:
            raise RuntimeError("O tar não tem estados.cepaberto.zip e cidades.cepaberto.zip.")
        _log(f"referência CEP Aberto: {len(states)} UFs, {len(cities):,} cidades")
        for member in members:
            base = Path(member.name).name
            if base in {"estados.cepaberto.zip", "cidades.cepaberto.zip"}:
                continue
            if not base.endswith(".zip"):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with zipfile.ZipFile(io.BytesIO(extracted.read())) as archive:
                for row in _csv_from_zip(archive):
                    if len(row) != 6:
                        continue
                    cep = normalize_cep(row[0])
                    if cep is None:
                        continue
                    city_name, state_id = cities.get(row[4], ("", ""))
                    uf = states.get(row[5]) or states.get(state_id)
                    logradouro = " ".join(row[1].split()) or None
                    bairro = " ".join(row[3].split()) or None
                    yield cep, {
                        "logradouro": logradouro,
                        "complemento": row[2],
                        "bairro": bairro,
                        "cidade": city_name or None,
                        "uf": uf,
                    }


def write_outputs(records: dict[str, dict], data_dir: Path, meta: dict) -> None:
    cep_dir = data_dir / "ceps"
    grid_dir = data_dir / "grid"
    cep_dir.mkdir(parents=True, exist_ok=True)
    grid_dir.mkdir(parents=True, exist_ok=True)
    for stale in cep_dir.glob("*.json"):
        stale.unlink()
    for stale in grid_dir.glob("*.json"):
        stale.unlink()

    by_prefix: dict[str, list[str]] = defaultdict(list)
    for cep in records:
        by_prefix[cep[:2]].append(cep)

    grid: dict[str, list] = defaultdict(list)
    partitions = []
    by_uf: Counter[str] = Counter()
    by_precisao: Counter[str] = Counter()
    by_fonte: Counter[str] = Counter()
    with_coord = 0
    for prefix in sorted(by_prefix):
        blob = {}
        for cep in sorted(by_prefix[prefix]):
            record = _ordered(records[cep])
            blob[cep] = record
            by_fonte[record["fonte"]] += 1
            precisao = record.get("precisao") or "null"
            by_precisao[precisao] += 1
            if record.get("uf"):
                by_uf[record["uf"]] += 1
            if record.get("lat") is not None:
                with_coord += 1
                grid[cell_id(record["lat"], record["lng"])].append([cep, record["lat"], record["lng"]])
        path = cep_dir / f"{prefix}.json"
        path.write_text(
            json.dumps(blob, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n",
            encoding="utf-8",
        )
        partitions.append(
            {
                "arquivo": f"ceps/{prefix}.json",
                "ceps": len(blob),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
        _log(f"escreveu {path.name}: {len(blob):,} CEPs, {path.stat().st_size / (1024 * 1024):.1f} MiB")
        del blob

    grid_bytes = 0
    for name in sorted(grid):
        rows = sorted(grid[name], key=lambda item: item[0])
        path = grid_dir / f"{name}.json"
        path.write_text(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n",
            encoding="utf-8",
        )
        grid_bytes += path.stat().st_size

    total = len(records)
    meta["gerado_em"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["gerador"] = f"cepdb {__version__}"
    meta["total_ceps"] = total
    meta["ceps_com_coordenadas"] = with_coord
    meta["ceps_sem_coordenadas"] = total - with_coord
    meta["pct_com_coordenadas"] = round(100.0 * with_coord / total, 4) if total else 0.0
    meta["particoes"] = partitions
    meta["grade"] = {"arquivos": len(grid), "bytes": grid_bytes}
    meta["grade_graus"] = GRID_DEG
    meta["ceps_por_uf"] = dict(sorted(by_uf.items()))
    meta["ceps_por_precisao"] = dict(sorted(by_precisao.items()))
    meta["ceps_por_fonte"] = dict(sorted(by_fonte.items()))
    (data_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def enrich(data_dir: Path, tar_path: Path) -> dict:
    data_dir = Path(data_dir)
    tar_path = Path(tar_path)
    if not tar_path.is_file():
        raise SystemExit(
            f"Dump do CEP Aberto não encontrado em {tar_path}. "
            "O site exige login; baixe o tar autenticado e passe --tar."
        )
    meta_path = data_dir / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"meta.json ausente em {data_dir}. Rode python -m cepdb.build antes.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    _log(f"carregando CNEFE de {data_dir}")
    records = load_cnefe(data_dir)
    cnefe_total = len(records)
    streets, bairros, municipios, by_name = index_cnefe(records)
    mun_official = {}
    for record in records.values():
        code = record.get("codigo_ibge")
        if code and record.get("cidade") and code not in mun_official:
            mun_official[code] = record["cidade"]
    for record in records.values():
        stamp_cnefe(record)

    stats = Counter()
    match_kinds = Counter()
    both = 0
    only_aberto = 0
    for cep, aberto in iter_cepaberto(tar_path):
        stats["linhas"] += 1
        codigo, kind = (None, "sem_cidade")
        if aberto.get("cidade") and aberto.get("uf"):
            codigo, kind = match_municipio(aberto["cidade"], aberto["uf"], by_name)
        match_kinds[kind] += 1
        if cep in records:
            both += 1
            before_log = records[cep].get("logradouro")
            before_bairro = records[cep].get("bairro")
            before_cidade = records[cep].get("cidade")
            apply_cepaberto_to_cnefe(records[cep], aberto, streets, bairros, municipios)
            origem = records[cep]["origem"]
            if before_log is None and origem["logradouro"] == "cepaberto":
                stats["preencheu_logradouro"] += 1
            if before_bairro is None and origem["bairro"] == "cepaberto":
                stats["preencheu_bairro"] += 1
            if records[cep].get("complemento"):
                stats["preencheu_complemento"] += 1
            if before_cidade is None and origem["cidade"] == "cepaberto":
                stats["preencheu_cidade"] += 1
            divergencias = records[cep].get("divergencias") or {}
            if "logradouro" in divergencias:
                stats["divergencia_logradouro"] += 1
            if "bairro" in divergencias:
                stats["divergencia_bairro"] += 1
            if "cidade" in divergencias:
                stats["divergencia_cidade"] += 1
        else:
            only_aberto += 1
            cidade = aberto.get("cidade")
            origem_cidade = "cepaberto" if cidade else None
            if codigo and kind == "exato":
                oficial = mun_official.get(codigo)
                if oficial:
                    cidade = oficial
                    origem_cidade = "cepaberto+ibge" if aberto.get("cidade") else "ibge"
            record = new_from_cepaberto(cep, aberto, codigo, cidade, origem_cidade, streets, bairros, municipios)
            records[cep] = record
            stats["novo_" + (record["precisao"] or "sem_coordenada")] += 1
        if stats["linhas"] % 200_000 == 0:
            _log(f"  {stats['linhas']:,} CEPs do dump | RSS {rss_mb():.0f} MiB")

    only_cnefe = cnefe_total - both
    _log(
        f"cruzamento: CNEFE {cnefe_total:,} | nos dois {both:,} | "
        f"só CNEFE {only_cnefe:,} | só CEP Aberto {only_aberto:,}"
    )

    fontes = [item for item in meta.get("fontes", []) if item.get("id") != FONTE_ABERTO]
    fontes.append(
        {
            "id": FONTE_ABERTO,
            "titulo": "CEP Aberto",
            "autor": "Mucelli Rezende Oliveira, Eduardo",
            "ano": 2014,
            "url": "https://www.cepaberto.com/",
            "dump": tar_path.name,
            "gerado_em": "2026-11-17",
            "sha256": _file_sha256(tar_path),
            "bytes": tar_path.stat().st_size,
            "ceps": stats["linhas"],
            "cidades": None,
            "licenca": "Open Database License (ODbL) 1.0",
            "licenca_url": "https://opendatacommons.org/licenses/odbl/1.0/",
            "atribuicao": "CEP Aberto (https://www.cepaberto.com/), Mucelli Rezende Oliveira, Eduardo, 2014. ODbL 1.0.",
            "observacao": (
                "O dump oficial não traz latitude nem longitude. O script não baixa o arquivo: "
                "o site exige sessão autenticada. O tar usado está em sources/cepaberto/."
            ),
            "obtencao": "Download manual, autenticado, do dump gerado em 2026-11-17. O tar versionado é esse arquivo.",
        }
    )
    meta["fontes"] = fontes
    meta["fontes_avaliadas_nao_usadas"] = [
        item for item in meta.get("fontes_avaliadas_nao_usadas", []) if item.get("id") != "cep-aberto"
    ]
    meta["licenca_base"] = (
        "A base combinada em data/ é uma base derivada sob a Open Database License (ODbL) 1.0, "
        "porque inclui o CEP Aberto. Obrigações: atribuir o CEP Aberto (Eduardo Mucelli Rezende Oliveira, 2014, "
        "https://www.cepaberto.com/) e o IBGE (CNEFE, Censo 2022), e compartilhar pela mesma licença qualquer "
        "base derivada que seja usada publicamente. O código em cepdb/ continua sob MIT e não cobre os dados. "
        "Os microdados do IBGE permanecem de domínio público com citação da fonte; a presença do CEP Aberto "
        "é o que submete o conjunto a ODbL."
    )
    meta["cepaberto"] = {
        "linhas": stats["linhas"],
        "ceps_nos_dois": both,
        "ceps_so_cnefe": only_cnefe,
        "ceps_so_cepaberto": only_aberto,
        "preencheu_logradouro": stats["preencheu_logradouro"],
        "preencheu_bairro": stats["preencheu_bairro"],
        "preencheu_complemento": stats["preencheu_complemento"],
        "preencheu_cidade": stats["preencheu_cidade"],
        "divergencia_logradouro": stats["divergencia_logradouro"],
        "divergencia_bairro": stats["divergencia_bairro"],
        "divergencia_cidade": stats["divergencia_cidade"],
        "casamento_municipio": dict(match_kinds),
        "novos_por_precisao": {
            key.removeprefix("novo_"): stats[key] for key in stats if key.startswith("novo_")
        },
        "metodo_coordenada_herdada": (
            "Para CEP ausente do CNEFE, a coordenada é o centroide ponderado por n_enderecos "
            "dos CEPs do CNEFE no mesmo município: primeiro os de mesmo logradouro e mesmo bairro "
            "(precisao logradouro; um número final no nome do CEP Aberto é ignorado só na comparação), "
            "depois os de mesmo bairro (precisao bairro), depois todos os do município (precisao municipio). "
            "Sem município IBGE reconhecido, lat/lng ficam nulos. CEP presente no CNEFE mantém a coordenada "
            "do censo (precisao cnefe_cep)."
        ),
        "regra_cidade": (
            "O nome de município vindo do CNEFE não é substituído quando o CEP Aberto traz outra localidade. "
            "A divergência fica em divergencias.cidade. Nome vazio é preenchido. Localidade no formato "
            "'Distrito (Município)' herda o código IBGE do município entre parênteses."
        ),
    }
    limitacoes = list(meta.get("limitacoes") or [])
    extra = [
        "A base combinada está sob ODbL 1.0 por causa do CEP Aberto. Atribuição e compartilhamento pela mesma licença são obrigatórios.",
        "Coordenada com precisao logradouro, bairro ou municipio não é a posição do CEP: é o centroide de outros CEPs do CNEFE com o mesmo nome de rua, de bairro ou do município.",
        "O dump do CEP Aberto não tem geolocalização. CEP sem município reconhecido no IBGE fica sem coordenada.",
        "divergencias guarda o texto do CNEFE quando o nome postal do CEP Aberto foi o escolhido, ou o nome de localidade do CEP Aberto quando a cidade oficial do IBGE foi mantida.",
    ]
    for item in extra:
        if item not in limitacoes:
            limitacoes.append(item)
    meta["limitacoes"] = limitacoes
    # O primeiro item da lista antiga diz que a base é só o CNEFE. Substitui.
    meta["limitacoes"][0] = (
        "Entram os CEPs do CNEFE 2022 e os do dump do CEP Aberto de 2026-11-17. "
        "Isso ainda não é o universo teórico de 8 dígitos nem necessariamente todo o DNE dos Correios."
    )

    write_outputs(records, data_dir, meta)
    _log(
        f"base combinada: {meta['total_ceps']:,} CEPs, "
        f"{meta['pct_com_coordenadas']}% com coordenada, precisão {meta['ceps_por_precisao']}"
    )
    return meta


def enrich_if_available(data_dir: Path) -> dict | None:
    tar_path = default_tar()
    if not tar_path.is_file():
        _log(f"tar do CEP Aberto ausente ({tar_path}); base permanece só com o CNEFE")
        return None
    return enrich(data_dir, tar_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cruza a base CNEFE com um dump local do CEP Aberto.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--tar", type=Path, default=None, help="Tar do dump. Padrão: sources/cepaberto/cepaberto_2026-11-17.tar")
    args = parser.parse_args(argv)
    tar_path = args.tar or default_tar()
    enrich(args.data_dir, tar_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
