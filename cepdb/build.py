"""Baixa o CNEFE 2022 e gera a base JSON particionada por prefixo de CEP.

Uso:
    python -m cepdb.build
    python -m cepdb.build --raw-dir /tmp/cnefe/uf --out-dir data
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from cepdb import __version__
from cepdb.aggregate import (
    FONTE,
    GRID_DEG,
    UF_BY_CODE,
    Accumulator,
    cell_id,
    finalize_record,
    multi_municipio,
)

IBGE_UF_URL = (
    "https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
    "Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/"
)
IBGE_AGREGADOS_URL = (
    "https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
    "Censo_Demografico_2022/Agregados_por_CEP/Agregados_por_CEP_CNEFE_Censo_2022.zip"
)
IBGE_MUNICIPIOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
USER_AGENT = "cepdb/1.0 (reproducible open-data build; source=IBGE CNEFE Censo 2022)"

# (codigo_uf, sigla, arquivo). Datas de referência do FTP consultado em 2026-11-18:
# a maior parte dos ZIP foi publicada em 2024-05-20; Rondônia foi atualizada em 2024-06-12.
UF_FILES = [
    ("11", "RO", "11_RO.zip"),
    ("12", "AC", "12_AC.zip"),
    ("13", "AM", "13_AM.zip"),
    ("14", "RR", "14_RR.zip"),
    ("15", "PA", "15_PA.zip"),
    ("16", "AP", "16_AP.zip"),
    ("17", "TO", "17_TO.zip"),
    ("21", "MA", "21_MA.zip"),
    ("22", "PI", "22_PI.zip"),
    ("23", "CE", "23_CE.zip"),
    ("24", "RN", "24_RN.zip"),
    ("25", "PB", "25_PB.zip"),
    ("26", "PE", "26_PE.zip"),
    ("27", "AL", "27_AL.zip"),
    ("28", "SE", "28_SE.zip"),
    ("29", "BA", "29_BA.zip"),
    ("31", "MG", "31_MG.zip"),
    ("32", "ES", "32_ES.zip"),
    ("33", "RJ", "33_RJ.zip"),
    ("35", "SP", "35_SP.zip"),
    ("41", "PR", "41_PR.zip"),
    ("42", "SC", "42_SC.zip"),
    ("43", "RS", "43_RS.zip"),
    ("50", "MS", "50_MS.zip"),
    ("51", "MT", "51_MT.zip"),
    ("52", "GO", "52_GO.zip"),
    ("53", "DF", "53_DF.zip"),
]

NEEDED_COLUMNS = (
    "CEP",
    "LATITUDE",
    "LONGITUDE",
    "NV_GEO_COORD",
    "COD_MUNICIPIO",
    "DSC_LOCALIDADE",
    "NOM_TIPO_SEGLOGR",
    "NOM_TITULO_SEGLOGR",
    "NOM_SEGLOGR",
)

PROGRESS_EVERY = 2_000_000


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return 0.0
    return 0.0


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def head_info(url: str) -> dict[str, str]:
    try:
        with urllib.request.urlopen(_request(url, "HEAD"), timeout=60) as response:
            return {key.lower(): value for key, value in response.headers.items()}
    except Exception as exc:  # noqa: BLE001 — o GET ainda pode funcionar se o HEAD falhar
        _log(f"HEAD falhou para {url}: {exc}")
        return {}


def download(url: str, dest: Path) -> dict[str, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    info = head_info(url)
    expected = info.get("content-length")
    if dest.is_file() and expected and dest.stat().st_size == int(expected):
        _log(f"já existe {dest.name} ({dest.stat().st_size} bytes)")
        return info
    tmp = dest.with_suffix(dest.suffix + ".part")
    _log(f"baixando {url}")
    started = time.time()
    with urllib.request.urlopen(_request(url), timeout=180) as response, tmp.open("wb") as handle:
        info = {key.lower(): value for key, value in response.headers.items()}
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    tmp.replace(dest)
    elapsed = time.time() - started
    _log(f"ok {dest.name} {dest.stat().st_size} bytes em {elapsed:.1f}s")
    return info


def load_municipios(path: Path) -> dict[str, dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, dict[str, str]] = {}
    for item in payload:
        micro = item.get("microrregiao") or {}
        meso = micro.get("mesorregiao") or {}
        uf = (meso.get("UF") or {}).get("sigla")
        if not uf:
            imediata = item.get("regiao-imediata") or {}
            inter = imediata.get("regiao-intermediaria") or {}
            uf = (inter.get("UF") or {}).get("sigla")
        code = str(item["id"]).zfill(7)
        table[code] = {"nome": item["nome"], "uf": uf or ""}
    return table


def load_agregados_ceps(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"ZIP de agregados inesperado: {archive.namelist()}")
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text, delimiter=";")
            header = next(reader)
            cep_index = header.index("CEP")
            return {row[cep_index].strip() for row in reader if row and row[cep_index].strip()}


def preflight_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"{path.name} deveria conter um CSV, veio {archive.namelist()}")
        with archive.open(names[0]) as raw:
            header = raw.readline().decode("utf-8-sig")
        for column in NEEDED_COLUMNS:
            if column not in header.split(";"):
                raise RuntimeError(f"{path.name} sem a coluna {column}")


def process_zip(path: Path, acc: Accumulator) -> None:
    started = time.time()
    rows_before = acc.rows
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.reader(text, delimiter=";")
            header = next(reader)
            index = {name: pos for pos, name in enumerate(header)}
            missing = [column for column in NEEDED_COLUMNS if column not in index]
            if missing:
                raise RuntimeError(f"{path.name} sem colunas {missing}")
            positions = [index[column] for column in NEEDED_COLUMNS]
            need = max(positions)
            i_cep, i_lat, i_lng, i_nv, i_mun, i_loc, i_tipo, i_tit, i_nome = positions
            add = acc.add
            next_mark = ((acc.rows // PROGRESS_EVERY) + 1) * PROGRESS_EVERY
            for row in reader:
                if len(row) <= need:
                    # CSV do IBGE às vezes omite campos vazios no fim da linha.
                    acc.short_rows += 1
                    row = row + [""] * (need + 1 - len(row))
                add(
                    row[i_cep],
                    row[i_lat],
                    row[i_lng],
                    row[i_nv],
                    row[i_mun],
                    row[i_loc],
                    row[i_tipo],
                    row[i_tit],
                    row[i_nome],
                )
                if acc.rows >= next_mark:
                    _log(
                        f"  {acc.rows:,} registros | {len(acc.aggs):,} CEPs | RSS {rss_mb():.0f} MiB"
                    )
                    next_mark += PROGRESS_EVERY
    elapsed = time.time() - started
    gained = acc.rows - rows_before
    _log(
        f"processado {path.name}: {gained:,} linhas em {elapsed:.1f}s "
        f"({gained / elapsed:,.0f} linhas/s) | RSS {rss_mb():.0f} MiB"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_dataset(acc: Accumulator, municipios: dict[str, dict[str, str]], out_dir: Path, source_meta: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cep_dir = out_dir / "ceps"
    grid_dir = out_dir / "grid"
    cep_dir.mkdir(parents=True, exist_ok=True)
    grid_dir.mkdir(parents=True, exist_ok=True)

    # Remove partições de uma geração anterior para não deixar prefixo obsoleto.
    for stale in cep_dir.glob("*.json"):
        stale.unlink()
    for stale in grid_dir.glob("*.json"):
        stale.unlink()

    by_prefix: dict[str, list[str]] = defaultdict(list)
    for cep in acc.aggs:
        by_prefix[cep[:2]].append(cep)

    grid: dict[str, list[list]] = defaultdict(list)
    by_uf: Counter[str] = Counter()
    by_nv: Counter[str] = Counter()
    partitions = []
    total = 0
    with_coord = 0
    without_logradouro = 0
    without_bairro = 0
    multi = 0
    sem_municipio = 0

    for prefix in sorted(by_prefix):
        blob = {}
        for cep in sorted(by_prefix[prefix]):
            state = acc.aggs[cep]
            record = finalize_record(cep, state, municipios)
            blob[cep] = record
            total += 1
            if record["lat"] is not None:
                with_coord += 1
                grid[cell_id(record["lat"], record["lng"])].append([cep, record["lat"], record["lng"]])
                nv_key = str(record["nv_geo"])
                by_nv[nv_key] += 1
            if not record["logradouro"]:
                without_logradouro += 1
            if not record["bairro"]:
                without_bairro += 1
            if record["uf"]:
                by_uf[record["uf"]] += 1
            else:
                sem_municipio += 1
            if multi_municipio(state):
                multi += 1
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

    grid_files = []
    for name in sorted(grid):
        rows = sorted(grid[name], key=lambda item: item[0])
        path = grid_dir / f"{name}.json"
        path.write_text(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n",
            encoding="utf-8",
        )
        grid_files.append({"arquivo": f"grid/{name}.json", "ceps": len(rows), "bytes": path.stat().st_size})

    pct = round(100.0 * with_coord / total, 4) if total else 0.0
    meta = {
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gerador": f"cepdb {__version__}",
        "total_ceps": total,
        "ceps_com_coordenadas": with_coord,
        "ceps_sem_coordenadas": total - with_coord,
        "pct_com_coordenadas": pct,
        "ceps_sem_logradouro": without_logradouro,
        "ceps_sem_bairro": without_bairro,
        "ceps_em_multiplos_municipios": multi,
        "ceps_sem_municipio_conhecido": sem_municipio,
        "registros_lidos": acc.rows,
        "registros_cep_invalido": acc.bad_cep,
        "registros_linha_curta": acc.short_rows,
        "registros_coordenada_rejeitada": acc.coord_rejected,
        "registros_com_coordenada_valida": acc.rows_with_coord,
        "metodo_coordenada": (
            "Centroide igual à média aritmética de latitude e de longitude dos registros "
            "CNEFE do CEP, usando somente o melhor nível de geocodificação disponível "
            "(menor NV_GEO_COORD). Níveis: 1 endereço original, 2 endereço modificado "
            "(apartamentos no mesmo número), 3 coordenada estimada, 4 face de quadra, "
            "5 localidade, 6 setor censitário. Não é a mediana: guardar todos os pontos "
            "para a mediana de ~100 milhões de endereços não cabe no gerador."
        ),
        "regra_logradouro": (
            "Modal do logradouro (tipo + título + nome) entre os endereços com nome. "
            "Fica nulo quando o modal não alcança 50% desses endereços, ou quando o "
            "CNEFE só tem 'SEM DENOMINACAO'. n_logradouros conta os nomes distintos."
        ),
        "regra_bairro": (
            "Modal de DSC_LOCALIDADE quando essa localidade cobre pelo menos 50% dos "
            "endereços do CEP e não é só o nome do município. O CNEFE não tem um campo "
            "separado de bairro; abaixo de 50% o campo fica nulo para não rotular o CEP "
            "com uma localidade minoritária."
        ),
        "grade_graus": GRID_DEG,
        "particoes": partitions,
        "grade": {
            "arquivos": len(grid_files),
            "bytes": sum(item["bytes"] for item in grid_files),
        },
        "ceps_por_uf": dict(sorted(by_uf.items())),
        "ceps_por_nv_geo": dict(sorted(by_nv.items(), key=lambda item: item[0])),
        "cobertura_por_uf_codigo": UF_BY_CODE,
        "fontes": [source_meta],
        "fontes_avaliadas_nao_usadas": fontes_nao_usadas(),
        "limitacoes": [
            "A base contém os CEPs que aparecem em pelo menos um endereço do CNEFE do Censo 2022, não o universo teórico de 100 milhões de combinações nem necessariamente todo o DNE dos Correios (caixas postais, grandes usuários e CEPs criados depois da coleta podem faltar).",
            "A coordenada é o centroide dos endereços recenseados, não a fachada de um número específico nem o centro geométrico do polígono do CEP.",
            "CEPs de município inteiro ou de área rural grande ficam com coordenada puxada para onde há mais endereços e, em geral, sem um logradouro único.",
            "NV_GEO_COORD 4, 5 ou 6 indica precisão de quadra, localidade ou setor censitário, não de endereço.",
            "n_enderecos conta registros do microdado (uma linha por espécie no endereço). O mesmo código de endereço pode repetir quando há mais de uma espécie; a repetição é pequena e não muda o centroide quando a coordenada é a mesma.",
        ],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return meta


def fontes_nao_usadas() -> list[dict]:
    return [
        {
            "id": "ibge-cnefe-agregados-por-cep",
            "data": "2024-06-14",
            "url": IBGE_AGREGADOS_URL,
            "motivo": (
                "Publicação oficial de 931.499 CEPs com contagem de endereços por espécie, "
                "sem latitude/longitude. Usada só para conferir o conjunto de CEPs, não como geometria."
            ),
        },
        {
            "id": "ibge-coordenadas-enderecos-2024-02",
            "data": "2024-02-02",
            "url": (
                "https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
                "Censo_Demografico_2022/Coordenadas_enderecos/UF/"
            ),
            "motivo": "Divulgação anterior só de coordenadas, substituída pelo microdado CSV de maio/junho de 2024, que traz CEP, endereço e NV_GEO_COORD.",
        },
        {
            "id": "cep-aberto",
            "data_consulta": "2026-11-18",
            "url": "https://www.cepaberto.com/",
            "licenca": "Open Database License (ODbL) 1.0, atribuição e compartilhamento pela mesma licença.",
            "motivo": (
                "Cobre cerca de 1,14 milhão de CEPs geolocalizados de forma colaborativa e segue recebendo "
                "atualizações pontuais, mas o dump completo exige cadastro e sessão autenticada. "
                "Não foi baixado neste ambiente. A licença permitiria redistribuir; a fonte oficial com "
                "coordenada de censo foi preferida."
            ),
        },
        {
            "id": "geonames-postal-BR",
            "data": "2026-11-17",
            "url": "https://download.geonames.org/export/zip/BR.zip",
            "licenca": "Creative Commons Attribution 4.0 (CC BY 4.0).",
            "motivo": (
                "Arquivo BR.zip consultado em 2026-11-18 tem 5.525 registros, em geral um CEP por "
                "município (sufixo 000), com coordenada de localidade. Não cobre CEP de logradouro."
            ),
        },
        {
            "id": "brasilapi",
            "url": "https://brasilapi.com.br/",
            "motivo": "API de consulta unitária, sem dump redistribuível. Não é fonte de carga.",
        },
        {
            "id": "correios-edne",
            "url": "https://www.correios.com.br/",
            "motivo": (
                "O Diretório Nacional de Endereços dos Correios é produto da empresa, com licença "
                "própria, e não traz coordenada oficial aberta. Não foi copiado."
            ),
        },
        {
            "id": "openaddresses-osm",
            "licenca": "Predominantemente ODbL para dados derivados do OpenStreetMap.",
            "motivo": (
                "OpenAddresses e OpenStreetMap têm CEP e coordenada de forma incompleta e heterogênea. "
                "O CNEFE é mais completo para o endereço recenseado em 2022 e tem licença de dados abertos do IBGE."
            ),
        },
    ]


def source_meta_from(files_info: list[dict], agregados_info: dict) -> dict:
    return {
        "id": FONTE,
        "titulo": "Cadastro Nacional de Endereços para Fins Estatísticos (CNEFE) — Censo Demográfico 2022",
        "publicador": "Instituto Brasileiro de Geografia e Estatística (IBGE)",
        "coleta": "2022",
        "publicacao_microdados_csv": "2024-05-21",
        "atualizacao_arquivo_rondonia": "2024-06-12",
        "publicacao_agregados_por_cep": "2024-06-14",
        "nota_metodologica_coordenadas": "2024-02-02 (Notas metodológicas n. 01/2024)",
        "nota_metodologica_cnefe": "2024-06-14 (Notas metodológicas n. 04/2024)",
        "url_microdados_uf": IBGE_UF_URL,
        "url_agregados": IBGE_AGREGADOS_URL,
        "url_municipios": IBGE_MUNICIPIOS_URL,
        "url_documentacao": "https://www.ibge.gov.br/estatisticas/sociais/populacao/38734-cadastro-nacional-de-enderecos-para-fins-estatisticos.html",
        "licenca": (
            "Dados abertos de domínio público, com citação obrigatória da fonte. "
            "O IBGE informou, em atendimento de 8 de janeiro de 2009 (protocolo 8171/2009), que os "
            "conteúdos do portal são de domínio público e que o uso em outros portais ou publicações "
            "está condicionado à citação da fonte. O FTP do CNEFE exibe a nota 'Todos os arquivos aqui "
            "disponíveis são públicos.' A divulgação segue a Política de Dados Abertos do Poder "
            "Executivo Federal (Decreto nº 8.777/2016). Esta base derivada exige a mesma atribuição ao IBGE."
        ),
        "citacao": "IBGE. Censo Demográfico 2022: Cadastro Nacional de Endereços para Fins Estatísticos (CNEFE). Microdados por UF em CSV, divulgação de 21/05/2024 (Rondônia atualizada em 12/06/2024).",
        "arquivos_uf": files_info,
        "arquivo_agregados": agregados_info,
        "nomes_de_municipio": "API de localidades do IBGE (servicodados.ibge.gov.br), baixada no momento da geração. O código de 7 dígitos vem do próprio CNEFE.",
    }


def compare_agregados(acc: Accumulator, oficial: set[str]) -> dict:
    nossos = set(acc.aggs)
    missing = sorted(oficial - nossos)
    extra = sorted(nossos - oficial)
    return {
        "ceps_agregados_oficiais": len(oficial),
        "ceps_so_nos_agregados": len(missing),
        "ceps_so_no_microdado": len(extra),
        "exemplos_so_nos_agregados": missing[:15],
        "exemplos_so_no_microdado": extra[:15],
    }


def selected_files(only: list[str] | None) -> list[tuple[str, str, str]]:
    if not only:
        return list(UF_FILES)
    wanted = {item.strip().upper() for item in only}
    chosen = [item for item in UF_FILES if item[0] in wanted or item[1] in wanted]
    if not chosen:
        known = ", ".join(code for code, _, _ in UF_FILES)
        raise SystemExit(f"Nenhuma UF corresponde a {sorted(wanted)}. Códigos: {known}")
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera a base JSON de CEPs a partir do CNEFE/IBGE.")
    parser.add_argument("--raw-dir", type=Path, default=Path("raw"))
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--only", nargs="*", help="Restringe às UFs (código ou sigla), para teste.")
    parser.add_argument("--skip-download", action="store_true", help="Usa os ZIP já presentes em --raw-dir.")
    parser.add_argument("--skip-agregados", action="store_true", help="Não baixa nem compara o CSV agregado por CEP.")
    args = parser.parse_args(argv)

    raw_dir = args.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    files = selected_files(args.only)
    files_info = []
    for _code, _sigla, name in files:
        url = IBGE_UF_URL + name
        dest = raw_dir / name
        if args.skip_download:
            if not dest.is_file():
                raise SystemExit(f"Arquivo ausente: {dest}")
            info = head_info(url)
        else:
            info = download(url, dest)
        files_info.append(
            {
                "arquivo": name,
                "url": url,
                "bytes": dest.stat().st_size,
                "last_modified": info.get("last-modified"),
            }
        )
        preflight_zip(dest)

    mun_path = raw_dir / "municipios.json"
    if not (args.skip_download and mun_path.is_file()):
        download(IBGE_MUNICIPIOS_URL, mun_path)
    municipios = load_municipios(mun_path)
    _log(f"municípios IBGE carregados: {len(municipios):,}")

    agregados_info: dict = {}
    oficial: set[str] | None = None
    if not args.skip_agregados and not args.only:
        ag_path = raw_dir / "Agregados_por_CEP_CNEFE_Censo_2022.zip"
        if not (args.skip_download and ag_path.is_file()):
            ag_headers = download(IBGE_AGREGADOS_URL, ag_path)
        else:
            ag_headers = head_info(IBGE_AGREGADOS_URL)
        oficial = load_agregados_ceps(ag_path)
        agregados_info = {
            "arquivo": ag_path.name,
            "url": IBGE_AGREGADOS_URL,
            "bytes": ag_path.stat().st_size,
            "last_modified": ag_headers.get("last-modified"),
            "ceps": len(oficial),
        }
        _log(f"agregados oficiais: {len(oficial):,} CEPs")

    acc = Accumulator()
    started = time.time()
    for _code, _sigla, name in files:
        process_zip(raw_dir / name, acc)
    _log(
        f"agregação concluída em {time.time() - started:.1f}s | "
        f"{len(acc.aggs):,} CEPs | CEP inválido {acc.bad_cep:,} | "
        f"coordenada rejeitada {acc.coord_rejected:,} | RSS {rss_mb():.0f} MiB"
    )

    source_meta = source_meta_from(files_info, agregados_info)
    meta = write_dataset(acc, municipios, args.out_dir, source_meta)
    if oficial is not None:
        comparison = compare_agregados(acc, oficial)
        meta["conferencia_agregados_por_cep"] = comparison
        (args.out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _log(
            "conferência com agregados oficiais: "
            f"só nos agregados {comparison['ceps_so_nos_agregados']:,}, "
            f"só no microdado {comparison['ceps_so_no_microdado']:,}"
        )
    _log(
        f"base em {args.out_dir}: {meta['total_ceps']:,} CEPs, "
        f"{meta['pct_com_coordenadas']}% com coordenada"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
