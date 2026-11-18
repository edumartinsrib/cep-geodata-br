"""Conferências da base gerada. Falham se `data/` ainda não foi construída."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from cepdb.aggregate import fold_key, valid_coord
from cepdb.query import Database, record_fields_ok

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


class DatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        meta_path = DATA / "meta.json"
        if not meta_path.is_file():
            raise unittest.SkipTest("data/meta.json ausente; rode python -m cepdb.build")
        cls.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cls.db = Database(DATA)

    def test_totais_batem_com_as_particoes(self):
        total = 0
        maior = 0
        for item in self.meta["particoes"]:
            path = DATA / item["arquivo"]
            self.assertTrue(path.is_file(), item["arquivo"])
            self.assertLess(path.stat().st_size, 45 * 1024 * 1024, item["arquivo"])
            self.assertEqual(path.stat().st_size, item["bytes"])
            blob = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(blob), item["ceps"])
            prefix = path.stem
            for cep, record in blob.items():
                self.assertEqual(cep, record["cep"])
                self.assertTrue(cep.startswith(prefix))
                self.assertEqual(len(cep), 8)
                self.assertTrue(cep.isdigit())
                self.assertTrue(record_fields_ok(record))
                self.assertEqual(record["fonte"], "ibge-cnefe-censo2022")
                if record["lat"] is not None:
                    self.assertTrue(valid_coord(record["lat"], record["lng"]))
            total += len(blob)
            maior = max(maior, path.stat().st_size)
        self.assertEqual(total, self.meta["total_ceps"])
        self.assertGreater(self.meta["total_ceps"], 800_000)
        self.assertGreater(self.meta["pct_com_coordenadas"], 95)
        self.assertLess(maior, 45 * 1024 * 1024)

    def test_avenida_paulista(self):
        record = self.db.require("01310-100")
        self.assertEqual(record["cidade"], "São Paulo")
        self.assertEqual(record["uf"], "SP")
        self.assertEqual(record["codigo_ibge"], "3550308")
        self.assertIsNotNone(record["logradouro"])
        self.assertIn("paulista", fold_key(record["logradouro"]))
        # Trecho central da Avenida Paulista (MASP/Trianon fica por volta de -23.561, -46.656).
        self.assertGreaterEqual(record["lat"], -23.60)
        self.assertLessEqual(record["lat"], -23.53)
        self.assertGreaterEqual(record["lng"], -46.70)
        self.assertLessEqual(record["lng"], -46.62)
        self.assertIn(record["nv_geo"], (1, 2))
        perto = self.db.near(record["lat"], record["lng"], radius_km=0.3, limit=1)
        self.assertEqual(perto[0]["cep"], "01310100")
        self.assertEqual(perto[0]["dist_km"], 0.0)

    def test_cep_generico_01001000_nao_e_so_a_praca_da_se(self):
        """No Correios, 01001-000 é a Praça da Sé. No CNEFE o mesmo CEP foi
        lançado em milhares de endereços espalhados por São Paulo, então o
        centroide não é a praça e não há bairro majoritário.
        """
        record = self.db.require("01001000")
        self.assertEqual(record["cidade"], "São Paulo")
        self.assertEqual(record["uf"], "SP")
        self.assertEqual(record["codigo_ibge"], "3550308")
        self.assertIsNone(record["logradouro"])
        self.assertIsNone(record["bairro"])
        self.assertGreater(record["n_enderecos"], 1000)
        self.assertGreater(record["n_logradouros"], 50)
        self.assertGreaterEqual(record["lat"], -23.70)
        self.assertLessEqual(record["lat"], -23.45)
        self.assertGreaterEqual(record["lng"], -46.80)
        self.assertLessEqual(record["lng"], -46.50)


if __name__ == "__main__":
    unittest.main()
