"""Interface de linha de comando: consulta e, via `python -m cepdb.build`, geração."""

from __future__ import annotations

import argparse
import json
import sys

from cepdb import __version__
from cepdb.query import BaseAusente, CepNaoEncontrado, Database


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cepdb",
        description="Consulta a base local de CEPs brasileiros com coordenadas (CNEFE/IBGE, Censo 2022).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exemplos:\n"
            "  python -m cepdb 01310100\n"
            "  python -m cepdb 01310-100\n"
            "  python -m cepdb --near -23.5614 -46.6559 --radius-km 0.4 --limit 5\n"
            "  python -m cepdb.build\n"
        ),
    )
    parser.add_argument("cep", nargs="*", help="Um ou mais CEPs (8 dígitos, hífen opcional).")
    parser.add_argument("--near", nargs=2, metavar=("LAT", "LNG"), type=float, help="Busca por proximidade.")
    parser.add_argument("--radius-km", type=float, default=5.0, help="Raio da busca por proximidade (padrão: 5).")
    parser.add_argument("--limit", type=int, default=10, help="Máximo de resultados na busca por proximidade (padrão: 10).")
    parser.add_argument("--data-dir", help="Diretório da base (padrão: ./data ou $CEPDB_DATA).")
    parser.add_argument("--version", action="version", version=f"cepdb {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.near is not None and args.cep:
        parser.error("informe um CEP ou --near, não os dois.")
    if args.near is None and not args.cep:
        parser.error("informe um CEP ou --near LAT LNG.")

    try:
        db = Database(args.data_dir)
    except BaseAusente as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        if args.near is not None:
            lat, lng = args.near
            results = db.near(lat, lng, radius_km=args.radius_km, limit=args.limit)
            json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            if not results:
                print("Nenhum CEP no raio informado.", file=sys.stderr)
                return 1
            return 0

        records = []
        missing = False
        for cep in args.cep:
            try:
                records.append(db.require(cep))
            except CepNaoEncontrado as exc:
                print(str(exc), file=sys.stderr)
                missing = True
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        if missing:
            return 1
        payload = records[0] if len(records) == 1 else records
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
