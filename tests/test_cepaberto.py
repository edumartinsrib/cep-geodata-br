import unittest

from cepdb.cepaberto import (
    _Bucket,
    choose_cidade,
    choose_text,
    clean_complemento,
    derive_coordinate,
    street_match_key,
)


class TextRulesTest(unittest.TestCase):
    def test_preenche_vazio_e_mantem_grafia_quando_concorda(self):
        display, origem, divergencia = choose_text(None, "Praça da Sé")
        self.assertEqual((display, origem, divergencia), ("Praça da Sé", "cepaberto", None))
        display, origem, divergencia = choose_text("AVENIDA PAULISTA", "Avenida Paulista")
        self.assertEqual(display, "Avenida Paulista")
        self.assertEqual(origem, "cnefe+cepaberto")
        self.assertIsNone(divergencia)

    def test_divergencia_guarda_o_texto_do_cnefe(self):
        display, origem, divergencia = choose_text("RUA DIREITA", "Rua São Bento")
        self.assertEqual(display, "Rua São Bento")
        self.assertEqual(origem, "cepaberto")
        self.assertEqual(divergencia, "RUA DIREITA")

    def test_cidade_oficial_nao_e_trocada_por_distrito(self):
        display, origem, divergencia = choose_cidade("São Paulo", "São Paulo")
        self.assertEqual((display, origem, divergencia), ("São Paulo", "cnefe+cepaberto", None))
        display, origem, divergencia = choose_cidade("Macapá", "Abacate da Pedreira (Macapá)")
        self.assertEqual(display, "Macapá")
        self.assertEqual(origem, "cnefe")
        self.assertEqual(divergencia, "Abacate da Pedreira (Macapá)")

    def test_complemento_remove_so_o_prefixo_do_dump(self):
        self.assertEqual(clean_complemento("- de 612 a 1510 - lado par"), "de 612 a 1510 - lado par")
        self.assertEqual(clean_complemento("- lado ímpar"), "lado ímpar")
        self.assertIsNone(clean_complemento("-"))
        self.assertIsNone(clean_complemento("   "))

    def test_numero_final_nao_entra_na_chave_da_rua(self):
        self.assertEqual(street_match_key("Avenida Paulista 52"), street_match_key("AVENIDA PAULISTA"))
        self.assertEqual(street_match_key("Praça da Sé 108"), street_match_key("Praça da Sé"))
        self.assertNotEqual(street_match_key("Rua 25 de Março"), street_match_key("Rua"))


class CoordinateFallbackTest(unittest.TestCase):
    def test_ordem_logradouro_bairro_municipio(self):
        streets = {}
        bairros = {}
        municipios = {}
        rua = _Bucket()
        rua.add(-23.56, -46.65, 10)
        streets[("3550308", street_match_key("Avenida Paulista"), "bela vista")] = rua
        bairro = _Bucket()
        bairro.add(-23.57, -46.64, 10)
        bairros[("3550308", "bela vista")] = bairro
        municipio = _Bucket()
        municipio.add(-23.55, -46.63, 10)
        municipios["3550308"] = municipio

        lat, lng, precisao = derive_coordinate(
            "3550308", "Avenida Paulista 52", "Bela Vista", streets, bairros, municipios
        )
        self.assertEqual(precisao, "logradouro")
        self.assertEqual((lat, lng), (-23.56, -46.65))

        lat, lng, precisao = derive_coordinate("3550308", "Rua Inexistente", "Bela Vista", streets, bairros, municipios)
        self.assertEqual(precisao, "bairro")
        self.assertEqual((lat, lng), (-23.57, -46.64))

        lat, lng, precisao = derive_coordinate("3550308", None, None, streets, bairros, municipios)
        self.assertEqual(precisao, "municipio")

        lat, lng, precisao = derive_coordinate(None, "Avenida Paulista", "Bela Vista", streets, bairros, municipios)
        self.assertIsNone(precisao)
        self.assertIsNone(lat)
        self.assertIsNone(lng)


if __name__ == "__main__":
    unittest.main()
