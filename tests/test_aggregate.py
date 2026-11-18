import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from cepdb.aggregate import (
    Accumulator,
    cell_id,
    compose_logradouro,
    finalize_record,
    haversine_km,
    normalize_cep,
    valid_coord,
)
from cepdb.build import process_zip, write_dataset
from cepdb.query import Database

MUNICIPIOS = {
    "3550308": {"nome": "São Paulo", "uf": "SP"},
    "1400100": {"nome": "Boa Vista", "uf": "RR"},
}


class NormalizeTest(unittest.TestCase):
    def test_hifen_e_espacos(self):
        self.assertEqual(normalize_cep("01310-100"), "01310100")
        self.assertEqual(normalize_cep(" 01310 100 "), "01310100")

    def test_rejeita_incompleto(self):
        self.assertIsNone(normalize_cep("1310100"))
        self.assertIsNone(normalize_cep(""))
        self.assertIsNone(normalize_cep("013101001"))

    def test_coordenada_fora_do_brasil(self):
        self.assertFalse(valid_coord(0.0, 0.0))
        self.assertFalse(valid_coord(48.8, 2.3))
        self.assertTrue(valid_coord(-23.56, -46.65))
        self.assertTrue(valid_coord(0.92, -29.3))

    def test_celula_negativa_usa_floor(self):
        self.assertEqual(cell_id(-23.0, -46.0), "-23_-46")
        self.assertEqual(cell_id(-23.0001, -46.0001), "-24_-47")

    def test_haversine_um_grau(self):
        distance = haversine_km(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(distance, 111.195, delta=0.05)

    def test_logradouro_sem_denominacao(self):
        self.assertIsNone(compose_logradouro("RUA", "", "SEM DENOMINACAO"))
        self.assertEqual(compose_logradouro("AVENIDA", "DOUTOR", "PAULISTA"), "AVENIDA DOUTOR PAULISTA")


class AggregateTest(unittest.TestCase):
    def test_centroide_ignora_nivel_pior_e_coordenada_invalida(self):
        acc = Accumulator()
        acc.add("01310-100", "-23.0", "-46.0", "1", "3550308", "BELA VISTA", "AVENIDA", "", "PAULISTA")
        acc.add("01310100", "-23.2", "-46.2", "1", "3550308", "BELA VISTA", "AVENIDA", "", "PAULISTA")
        acc.add("01310100", "-10.0", "-40.0", "6", "3550308", "BELA VISTA", "RUA", "", "OUTRA")
        acc.add("01310100", "0", "0", "1", "3550308", "BELA VISTA", "AVENIDA", "", "PAULISTA")
        acc.add("123", "-23.0", "-46.0", "1", "3550308", "BELA VISTA", "RUA", "", "X")

        self.assertEqual(len(acc.aggs), 1)
        self.assertEqual(acc.bad_cep, 1)
        self.assertEqual(acc.coord_rejected, 1)
        record = finalize_record("01310100", acc.aggs["01310100"], MUNICIPIOS)
        self.assertEqual(record["cidade"], "São Paulo")
        self.assertEqual(record["uf"], "SP")
        self.assertEqual(record["codigo_ibge"], "3550308")
        self.assertEqual(record["bairro"], "BELA VISTA")
        self.assertEqual(record["logradouro"], "AVENIDA PAULISTA")
        self.assertEqual(record["n_logradouros"], 2)
        self.assertEqual(record["nv_geo"], 1)
        self.assertAlmostEqual(record["lat"], -23.1, places=6)
        self.assertAlmostEqual(record["lng"], -46.1, places=6)
        self.assertEqual(record["n_enderecos"], 4)
        self.assertEqual(record["fonte"], "ibge-cnefe-censo2022")

    def test_bairro_igual_ao_municipio_fica_nulo_e_rua_sem_maioria_tambem(self):
        acc = Accumulator()
        for nome in ("UM", "DOIS", "TRES"):
            acc.add("69300000", "2.82", "-60.67", "1", "1400100", "Boa Vista", "RUA", "", nome)
        record = finalize_record("69300000", acc.aggs["69300000"], MUNICIPIOS)
        self.assertIsNone(record["bairro"])
        self.assertIsNone(record["logradouro"])
        self.assertEqual(record["n_logradouros"], 3)
        self.assertEqual(record["cidade"], "Boa Vista")

    def test_bairro_sem_maioria_fica_nulo(self):
        acc = Accumulator()
        for localidade in ("CENTRO", "SAO RAFAEL", "IGUATEMI"):
            acc.add("01001000", "-23.55", "-46.63", "1", "3550308", localidade, "PRACA", "", "DA SE")
        record = finalize_record("01001000", acc.aggs["01001000"], MUNICIPIOS)
        self.assertIsNone(record["bairro"])
        self.assertEqual(record["logradouro"], "PRACA DA SE")


class BuildRoundtripTest(unittest.TestCase):
    def test_zip_gera_particao_e_consulta(self):
        header = (
            "CEP;LATITUDE;LONGITUDE;NV_GEO_COORD;COD_MUNICIPIO;DSC_LOCALIDADE;"
            "NOM_TIPO_SEGLOGR;NOM_TITULO_SEGLOGR;NOM_SEGLOGR"
        )
        rows = [
            "01310-100;-23.56;-46.66;1;3550308;BELA VISTA;AVENIDA;;PAULISTA",
            "01310100;-23.57;-46.65;1;3550308;BELA VISTA;AVENIDA;;PAULISTA",
            "69300-000;2.82;-60.67;5;1400100;Boa Vista;RUA;;UM",
            "69300000;2.84;-60.69;5;1400100;Boa Vista;RUA;;DOIS",
            "69300000;2.80;-60.65;5;1400100;Boa Vista;RUA;;TRES",
        ]
        payload = (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "amostra.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("amostra.csv", payload)
            acc = Accumulator()
            process_zip(zip_path, acc)
            out = root / "data"
            meta = write_dataset(acc, MUNICIPIOS, out, {"id": "teste"})
            self.assertEqual(meta["total_ceps"], 2)
            self.assertEqual(meta["ceps_com_coordenadas"], 2)
            db = Database(out)
            paulista = db.require("01310-100")
            self.assertEqual(paulista["logradouro"], "AVENIDA PAULISTA")
            self.assertEqual(paulista["uf"], "SP")
            self.assertAlmostEqual(paulista["lat"], -23.565, places=6)
            perto = db.near(paulista["lat"], paulista["lng"], radius_km=0.2, limit=3)
            self.assertEqual(perto[0]["cep"], "01310100")
            self.assertEqual(perto[0]["dist_km"], 0.0)
            self.assertTrue((out / "ceps" / "01.json").is_file())
            self.assertTrue((out / "ceps" / "69.json").is_file())
            saved = json.loads((out / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["total_ceps"], 2)


if __name__ == "__main__":
    unittest.main()
