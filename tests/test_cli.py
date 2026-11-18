import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from cepdb.aggregate import Accumulator
from cepdb.build import process_zip, write_dataset

ROOT = Path(__file__).resolve().parent.parent
MUNICIPIOS = {"3550308": {"nome": "São Paulo", "uf": "SP"}}


def _base_minima(root: Path) -> Path:
    header = (
        "CEP;LATITUDE;LONGITUDE;NV_GEO_COORD;COD_MUNICIPIO;DSC_LOCALIDADE;"
        "NOM_TIPO_SEGLOGR;NOM_TITULO_SEGLOGR;NOM_SEGLOGR"
    )
    line = "01310100;-23.561;-46.656;1;3550308;BELA VISTA;AVENIDA;;PAULISTA"
    blob = (header + "\n" + line + "\n").encode("utf-8")
    zip_path = root / "amostra.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("amostra.csv", blob)
    acc = Accumulator()
    process_zip(zip_path, acc)
    out = root / "data"
    write_dataset(acc, MUNICIPIOS, out, {"id": "teste"})
    return out


class CliTest(unittest.TestCase):
    def test_consulta_e_proximidade(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _base_minima(Path(tmp))
            env = os.environ.copy()
            env["CEPDB_DATA"] = str(data)
            lookup = subprocess.run(
                [sys.executable, "-m", "cepdb", "01310-100"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(lookup.returncode, 0, lookup.stderr)
            record = json.loads(lookup.stdout)
            self.assertEqual(record["cidade"], "São Paulo")
            self.assertEqual(record["uf"], "SP")
            self.assertIn("PAULISTA", record["logradouro"])

            missing = subprocess.run(
                [sys.executable, "-m", "cepdb", "00000000"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 1)

            near = subprocess.run(
                [sys.executable, "-m", "cepdb", "--near", "-23.561", "-46.656", "--radius-km", "1", "--limit", "1"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(near.returncode, 0, near.stderr)
            found = json.loads(near.stdout)
            self.assertEqual(found[0]["cep"], "01310100")

            invalid = subprocess.run(
                [sys.executable, "-m", "cepdb", "123"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
