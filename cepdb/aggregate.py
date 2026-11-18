"""Normalização e agregação de endereços do CNEFE por CEP."""

from __future__ import annotations

import math
import sys
import unicodedata

# Extensão aproximada do território brasileiro, incluindo ilhas oceânicas
# (São Pedro e São Paulo, Fernando de Noronha, Trindade).
LAT_MIN = -35.5
LAT_MAX = 6.5
LNG_MIN = -74.5
LNG_MAX = -28.0

GRID_DEG = 1

# Logradouro e bairro modais só entram no registro quando representam ao menos
# esta fração dos endereços que têm aquele atributo. Abaixo disso o CEP mistura
# ruas ou localidades demais para um único nome ser representativo.
MODAL_MIN_SHARE = 0.5

FONTE = "ibge-cnefe-censo2022"

UF_BY_CODE = {
    "11": "RO",
    "12": "AC",
    "13": "AM",
    "14": "RR",
    "15": "PA",
    "16": "AP",
    "17": "TO",
    "21": "MA",
    "22": "PI",
    "23": "CE",
    "24": "RN",
    "25": "PB",
    "26": "PE",
    "27": "AL",
    "28": "SE",
    "29": "BA",
    "31": "MG",
    "32": "ES",
    "33": "RJ",
    "35": "SP",
    "41": "PR",
    "42": "SC",
    "43": "RS",
    "50": "MS",
    "51": "MT",
    "52": "GO",
    "53": "DF",
}

# Nomes que o CNEFE usa quando o logradouro não foi identificado.
_MISSING_STREET = {
    "SEM DENOMINACAO",
    "SEM DENOMINAÇÃO",
    "SEM DENOMINACAO OFICIAL",
    "SEM DENOMINAÇÃO OFICIAL",
    "NAO INFORMADO",
    "NÃO INFORMADO",
    "NAO IDENTIFICADO",
    "NÃO IDENTIFICADO",
    "SEM NOME",
    "SEM INFORMACAO",
    "SEM INFORMAÇÃO",
    "DESCONHECIDO",
    "IGNORADO",
    "S/N",
    "SN",
}


def fold_key(value: str) -> str:
    """Comparação estável: sem acento, sem caixa, espaços colapsados."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


_MISSING_STREET_FOLDED = {fold_key(value) for value in _MISSING_STREET}


def normalize_cep(value: str) -> str | None:
    """Devolve o CEP com 8 dígitos ou None se não for um CEP completo.

    Hífen, espaços e outros separadores são descartados. Não se completa
    com zero à esquerda: um valor com menos de 8 dígitos é rejeitado para
    não inventar CEP.
    """
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) != 8:
        return None
    return digits


def valid_coord(lat: float, lng: float) -> bool:
    if lat == 0.0 and lng == 0.0:
        return False
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def parse_coord(lat_s: str, lng_s: str) -> tuple[float, float] | None:
    if not lat_s or not lng_s:
        return None
    try:
        lat = float(lat_s.replace(",", "."))
        lng = float(lng_s.replace(",", "."))
    except ValueError:
        return None
    if not valid_coord(lat, lng):
        return None
    return lat, lng


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _missing_name(value: str) -> bool:
    if not value:
        return True
    upper = value.upper()
    if upper in _MISSING_STREET:
        return True
    if not upper.isascii() and fold_key(value) in _MISSING_STREET_FOLDED:
        return True
    return False


def compose_logradouro(tipo: str | None, titulo: str | None, nome: str | None) -> str | None:
    """Monta 'TIPO TITULO NOME' como publicado, ou None se o nome não existe."""
    nome_c = _clean(nome)
    if _missing_name(nome_c):
        return None
    parts: list[str] = []
    for piece in (tipo, titulo):
        piece_c = _clean(piece)
        if piece_c and not _missing_name(piece_c):
            parts.append(piece_c)
    parts.append(nome_c)
    return " ".join(parts)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def cell_id(lat: float, lng: float, deg: int = GRID_DEG) -> str:
    """Célula da grade: canto sudoeste, em graus inteiros quando deg=1.

    `math.floor` é obrigatório porque `int` trunca em direção ao zero e
    colocaria a latitude -23.2 na célula -23 em vez de -24.
    """
    ilat = math.floor(lat / deg)
    ilng = math.floor(lng / deg)
    return f"{ilat}_{ilng}"


def _bump(primary: str | None, count: int, extra: dict[str, int] | None, value: str | None):
    if not value:
        return primary, count, extra
    if primary is None:
        return value, 1, extra
    if value == primary:
        return primary, count + 1, extra
    if extra is None:
        extra = {value: 1}
    else:
        extra[value] = extra.get(value, 0) + 1
    return primary, count, extra


def _mode(primary: str | None, count: int, extra: dict[str, int] | None) -> tuple[str | None, int, int]:
    """Retorna (valor modal, contagem do modal, total de ocorrências)."""
    best = primary
    best_n = count
    total = count
    if extra:
        for key, qty in extra.items():
            total += qty
            if qty > best_n:
                best = key
                best_n = qty
    if best_n <= 0:
        return None, 0, 0
    return best, best_n, total


def _nunique(primary: str | None, extra: dict[str, int] | None) -> int:
    n = 0 if primary is None else 1
    if extra:
        n += len(extra)
    return n


class _Cep:
    __slots__ = (
        "n",
        "best_nv",
        "sum_lat",
        "sum_lng",
        "n_coord",
        "mun",
        "mun_n",
        "mun_extra",
        "loc",
        "loc_n",
        "loc_extra",
        "street",
        "street_n",
        "street_extra",
    )

    def __init__(self) -> None:
        self.n = 0
        self.best_nv = 99
        self.sum_lat = 0.0
        self.sum_lng = 0.0
        self.n_coord = 0
        self.mun: str | None = None
        self.mun_n = 0
        self.mun_extra: dict[str, int] | None = None
        self.loc: str | None = None
        self.loc_n = 0
        self.loc_extra: dict[str, int] | None = None
        self.street: str | None = None
        self.street_n = 0
        self.street_extra: dict[str, int] | None = None


class Accumulator:
    """Agrega endereços em memória, um estado por CEP."""

    def __init__(self) -> None:
        self.aggs: dict[str, _Cep] = {}
        self.rows = 0
        self.bad_cep = 0
        self.short_rows = 0
        self.coord_rejected = 0
        self.rows_with_coord = 0

    def add(
        self,
        cep: str,
        lat_s: str,
        lng_s: str,
        nv_s: str,
        mun: str,
        loc: str,
        tipo: str,
        titulo: str,
        nome: str,
    ) -> None:
        self.rows += 1
        normalized = normalize_cep(cep)
        if normalized is None:
            self.bad_cep += 1
            return
        state = self.aggs.get(normalized)
        if state is None:
            state = _Cep()
            self.aggs[normalized] = state
        state.n += 1

        mun_c = _clean(mun)
        if mun_c:
            mun_c = sys.intern(mun_c)
        state.mun, state.mun_n, state.mun_extra = _bump(state.mun, state.mun_n, state.mun_extra, mun_c or None)
        loc_c = _clean(loc)
        if loc_c:
            loc_c = sys.intern(loc_c)
        state.loc, state.loc_n, state.loc_extra = _bump(state.loc, state.loc_n, state.loc_extra, loc_c or None)
        street = compose_logradouro(tipo, titulo, nome)
        if street:
            street = sys.intern(street)
        state.street, state.street_n, state.street_extra = _bump(
            state.street, state.street_n, state.street_extra, street
        )

        parsed = parse_coord(lat_s, lng_s)
        if parsed is None:
            if lat_s or lng_s:
                self.coord_rejected += 1
            return
        lat, lng = parsed
        try:
            nv = int(nv_s)
        except (TypeError, ValueError):
            nv = 9
        if nv < 1 or nv > 6:
            nv = 9
        if state.n_coord == 0 or nv < state.best_nv:
            state.best_nv = nv
            state.sum_lat = lat
            state.sum_lng = lng
            state.n_coord = 1
        elif nv == state.best_nv:
            state.sum_lat += lat
            state.sum_lng += lng
            state.n_coord += 1
        self.rows_with_coord += 1


def finalize_record(cep: str, state: _Cep, municipios: dict[str, dict[str, str]]) -> dict:
    mun_code, _, _ = _mode(state.mun, state.mun_n, state.mun_extra)
    info = municipios.get(mun_code) if mun_code else None
    cidade = info["nome"] if info else None
    if info and info.get("uf"):
        uf = info["uf"]
    elif mun_code and len(mun_code) >= 2:
        uf = UF_BY_CODE.get(mun_code[:2])
    else:
        uf = None

    localidade, loc_n, loc_total = _mode(state.loc, state.loc_n, state.loc_extra)
    bairro = None
    if localidade and loc_total and (loc_n / loc_total) >= MODAL_MIN_SHARE:
        if not cidade or fold_key(localidade) != fold_key(cidade):
            bairro = localidade

    street, street_n, named = _mode(state.street, state.street_n, state.street_extra)
    n_logradouros = _nunique(state.street, state.street_extra)
    logradouro = None
    if street and named and (street_n / named) >= MODAL_MIN_SHARE:
        logradouro = street

    lat = None
    lng = None
    nv_geo = None
    if state.n_coord:
        lat = round(state.sum_lat / state.n_coord, 6)
        lng = round(state.sum_lng / state.n_coord, 6)
        nv_geo = state.best_nv

    return {
        "cep": cep,
        "logradouro": logradouro,
        "complemento": None,
        "bairro": bairro,
        "cidade": cidade,
        "uf": uf,
        "codigo_ibge": mun_code,
        "lat": lat,
        "lng": lng,
        "precisao": "cnefe_cep" if lat is not None else None,
        "fonte": FONTE,
        "origem": {
            "logradouro": "cnefe" if logradouro else None,
            "complemento": None,
            "bairro": "cnefe" if bairro else None,
            "cidade": "cnefe" if cidade else None,
            "coordenada": "cnefe" if lat is not None else None,
        },
        "n_enderecos": state.n,
        "nv_geo": nv_geo,
        "n_logradouros": n_logradouros,
    }


def multi_municipio(state: _Cep) -> bool:
    return bool(state.mun_extra)
