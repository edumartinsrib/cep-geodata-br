# cepdb

Base local de CEPs do Brasil com latitude e longitude, agregada a partir do
[CNEFE do Censo Demográfico 2022](https://www.ibge.gov.br/estatisticas/sociais/populacao/38734-cadastro-nacional-de-enderecos-para-fins-estatisticos.html)
(IBGE). Cada CEP vira um registro com o logradouro mais representativo, a
localidade, o município, a UF, o código IBGE e o centroide dos endereços
recenseados.

Não é o Diretório Nacional de Endereços dos Correios e não enumera as
100 milhões de combinações teóricas de 8 dígitos. Entram os CEPs que
aparecem em pelo menos um endereço do CNEFE.

## Cobertura desta geração

Gerada em 2026-11-18 a partir dos CSV do IBGE. Os mesmos números estão em
[`data/meta.json`](data/meta.json).

- **931.499 CEPs**, exatamente o conjunto do arquivo oficial de agregados por CEP (14/06/2024). Nenhum CEP a mais, nenhum a menos.
- **111.102.875** registros de endereço lidos. Nenhum CEP inválido e nenhuma coordenada rejeitada.
- **100%** dos CEPs têm latitude e longitude.
- **99,84%** (929.975) usam coordenada de endereço original do censo (`nv_geo` 1). Os demais: 760 no nível 2, 295 no 3, 447 no 4 e 22 no 6. Nenhum CEP ficou só com o nível 5.
- **96,5%** têm logradouro (32.314 ficam sem, porque não há nome majoritário ou o CNEFE só diz `SEM DENOMINACAO`).
- **96,3%** têm bairro/localidade majoritária (34.055 ficam sem).
- 398 CEPs aparecem em mais de um município; o registro fica com o município mais frequente.
- As 27 UFs estão presentes. São Paulo tem 275.655 CEPs, Minas Gerais 102.503, Rio de Janeiro 77.419.
- 98 partições em `data/ceps/`. Não há CEPs começando com `00` nem com `10`. O maior arquivo é `ceps/13.json` (60.814 CEPs, 15,1 MB). A base inteira ocupa cerca de 261 MB, sendo 228 MB de CEPs e 31 MB da grade geográfica (632 células de 1°, a maior com 3,6 MB).

`01310-100` sai como Avenida Paulista, Bela Vista, São Paulo/SP, código IBGE
`3550308`, centroide **-23,565807, -46,651293** (nível 1, 1.811 endereços, um
único logradouro). O ponto fica no corredor da avenida: os endereços desse
CEP vão de -23,571 a -23,555 de latitude e de -46,664 a -46,645 de longitude,
a cerca de 700 m do MASP. O ViaCEP descreve o mesmo código como Avenida
Paulista, lado par, Bela Vista — a consulta foi só conferência de nome, não
fonte da base.

## Consulta

```bash
python -m cepdb 01310100
python -m cepdb 01310-100
python -m cepdb --near -23.5614 -46.6559 --radius-km 0.4 --limit 5
```

O CEP `01310-100` é a Avenida Paulista, em São Paulo/SP (código IBGE
`3550308`), com coordenada no trecho central da avenida.

Na biblioteca:

```python
from cepdb.query import Database

db = Database()
print(db.lookup("01310-100"))
print(db.near(-23.5614, -46.6559, radius_km=0.4, limit=5))
```

`lookup` devolve um dicionário ou `None`. A busca por proximidade acrescenta
`dist_km`. O diretório da base é `./data`, ou o caminho em `CEPDB_DATA` /
`--data-dir`.

Campos de cada CEP:

| Campo | Conteúdo |
| --- | --- |
| `cep` | 8 dígitos, sem hífen |
| `logradouro` | tipo + título + nome, quando um nome cobre pelo menos metade dos endereços nomeados do CEP; senão `null` |
| `bairro` | localidade do CNEFE (`DSC_LOCALIDADE`) quando ela cobre pelo menos metade dos endereços do CEP e não é só o nome do município; senão `null` |
| `cidade` | nome oficial do município na API de localidades do IBGE |
| `uf` | sigla |
| `codigo_ibge` | código do município com 7 dígitos |
| `lat`, `lng` | centroide em graus decimais, 6 casas, ou `null` |
| `fonte` | `ibge-cnefe-censo2022` |
| `n_enderecos` | linhas do microdado agregadas nesse CEP |
| `nv_geo` | melhor nível de geocodificação usado no centroide (1 é o melhor) |
| `n_logradouros` | quantidade de nomes de logradouro distintos |

## Layout dos arquivos

```
data/meta.json          totais, licença, método, hashes das partições
data/ceps/01.json       CEPs cujo prefixo é 01, objeto indexado pelo CEP
data/ceps/69.json       ...
data/grid/-24_-47.json  grade de 1° só com cep, lat, lng (busca por proximidade)
```

As partições ficam bem abaixo de 50 MB (a maior tem 15 MB), em JSON sem
compressão, para a consulta abrir um único arquivo. Não foi preciso Git LFS
nem `.json.gz`. O total versionado da base é cerca de 261 MB.

## Fontes

### Usada

**IBGE — CNEFE, Censo Demográfico 2022, microdados CSV por UF.**

- Diretório: <https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/>
- Coleta: 2022.
- Publicação dos CSV: 21/05/2024. O ZIP de Rondônia (`11_RO.zip`) foi atualizado em 12/06/2024.
- Cada endereço traz CEP, componentes do logradouro, localidade, código do município, latitude, longitude e `NV_GEO_COORD`.
- Nomes de município: API de localidades do IBGE (`https://servicodados.ibge.gov.br/api/v1/localidades/municipios`), baixada na geração. O código de 7 dígitos é o do próprio CNEFE.

**Licença.** Dados abertos de domínio público, com citação da fonte. Em
08/01/2009 o atendimento do IBGE (protocolo 8171/2009) afirmou que os
conteúdos do portal são de domínio público e que o uso em outros portais
ou publicações depende da citação da fonte. O FTP avisa que os arquivos
são públicos. A divulgação segue o Decreto nº 8.777/2016. Veja `NOTICE`.

Citação:

> IBGE. Censo Demográfico 2022: Cadastro Nacional de Endereços para Fins Estatísticos (CNEFE). Microdados por UF em CSV, 21/05/2024 (Rondônia atualizada em 12/06/2024).

O centroide **não** é uma coordenada publicada pelo IBGE. Ele é calculado
por este repositório.

Conferência de cobertura: o CSV de agregados por CEP do mesmo censo
(14/06/2024, 931.499 CEPs, sem coordenada) é baixado só para comparar o
conjunto de chaves. Resultado em `data/meta.json`, campo
`conferencia_agregados_por_cep`.

### Avaliadas e não usadas como geometria

| Fonte | Data consultada | Licença | Por que ficou de fora |
| --- | --- | --- | --- |
| [Agregados do CNEFE por CEP](https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Agregados_por_CEP/) | publicação 14/06/2024 | mesma licença IBGE | 931.499 CEPs e contagens, sem latitude/longitude. Serve de auditoria. |
| Coordenadas de endereços do IBGE | 02/02/2024 | mesma licença IBGE | Divulgação anterior, substituída pelo CSV de maio/junho de 2024, que já traz CEP e `NV_GEO_COORD`. |
| [CEP Aberto](https://www.cepaberto.com/) | página consultada em 18/11/2026 | ODbL 1.0 (atribuição e compartilhamento pela mesma licença) | Cerca de 1,14 milhão de CEPs colaborativos, com atualizações pontuais até 2026, mas o dump exige cadastro e sessão. Não foi baixado aqui. A fonte oficial do censo foi preferida. |
| [GeoNames BR.zip](https://download.geonames.org/export/zip/BR.zip) | arquivo de 17/11/2026, lido em 18/11/2026 | CC BY 4.0 | 5.525 registros, em geral um CEP por município (sufixo 000). Não cobre logradouro. |
| [BrasilAPI](https://brasilapi.com.br/) | 18/11/2026 | API, sem dump redistribuível | Consulta de um CEP por vez. Não entra na carga. |
| e-DNE dos Correios | — | licença da empresa, não é dado aberto | Base postal oficial, sem coordenada aberta e sem permissão clara de redistribuição. Não foi copiada. |
| OpenAddresses / OpenStreetMap | — | em geral ODbL para o que vem do OSM | CEP e coordenada incompletos e heterogêneos diante do CNEFE. |

## Como a coordenada é calculada

Para cada CEP, o gerador percorre os endereços do microdado e:

1. normaliza o CEP para 8 dígitos, descartando hífen; valores que não têm 8 dígitos são ignorados, sem completar zero à esquerda;
2. descarta coordenada ausente, `(0, 0)` ou fora do retângulo aproximado do Brasil (latitude de -35,5 a 6,5, longitude de -74,5 a -28,0), que inclui as ilhas oceânicas;
3. calcula a **média aritmética** de latitude e de longitude usando só o melhor `NV_GEO_COORD` daquele CEP.

Níveis do IBGE, do mais preciso ao mais grosseiro:

1. coordenada original do endereço no Censo 2022
2. coordenada modificada (apartamentos no mesmo número)
3. coordenada estimada
4. face de quadra
5. localidade
6. setor censitário

Se o CEP tem pontos de nível 1, os de nível 6 não entram na média. O campo
`nv_geo` guarda o nível usado. A média em graus é um centroide adequado
porque um CEP de logradouro ocupa uma área pequena. Não é a mediana: guardar
cada ponto para a mediana de cerca de 100 milhões de endereços não cabe no
gerador.

O logradouro fica de fora quando o nome mais frequente não chega a 50% dos
endereços que têm nome, o que é o caso típico de CEP único de município.
`SEM DENOMINACAO` não conta como nome. A mesma regra de 50% vale para o
bairro: a localidade modal só é gravada quando é majoritária. No CEP
`01001-000`, que nos Correios é a Praça da Sé, o CNEFE registrou 6.999
endereços em 109 localidades (a mais frequente tem 6,5%) e a latitude vai
de -23,93 a -23,42. O centroide fica dentro de São Paulo, a cerca de 4 km
da praça, e `bairro` e `logradouro` ficam nulos. Já `01310-100` tem um
único logradouro, Avenida Paulista, de ponta a ponta do trecho recenseado.

## Limitações

- Faltam CEPs que o CNEFE não viu: caixa postal, grande usuário, unidade dos Correios e códigos criados depois da coleta de 2022. O e-DNE dos Correios é maior e não é aberto.
- A coordenada é o centro dos endereços recenseados, não a porta de um número nem o centro de um polígono do CEP. Onde há mais endereços, o ponto é puxado para lá.
- CEP de cidade inteira ou de zona rural extensa pode ficar sem logradouro único e com `nv_geo` alto. Trate `nv_geo` 4, 5 ou 6 como precisão de quadra, localidade ou setor.
- O CNEFE não tem campo de bairro. `bairro` reutiliza a localidade censitária e some quando ela repete o nome do município.
- `n_enderecos` conta linhas do microdado (uma espécie de endereço por linha). O mesmo código de endereço pode repetir quando há mais de uma espécie; se a coordenada é a mesma, o centroide não muda.
- Grafia de logradouro e localidade permanece como o IBGE publicou, em geral em maiúsculas e sem acento. O nome do município vem da API do IBGE, com a grafia oficial.

## Regenerar

É preciso Python 3.11 ou superior, rede e espaço para cerca de 4 GB de ZIP
(o CSV descompactado passa de 18 GB e é lido em fluxo, sem ser gravado).
A agregação nacional leva alguns minutos e fica na casa de 1–2 GB de RAM.

```bash
python -m cepdb.build
```

Os ZIP vão para `raw/` (fora do Git). A base sai em `data/`. Para repetir
com arquivos já baixados:

```bash
python -m cepdb.build --raw-dir raw --skip-download
```

`--only 35` (ou `SP`) processa uma UF e **regrava** `data/` só com ela.
Não use isso se quiser conservar a base nacional.

O script confere o conjunto de CEPs com o arquivo oficial de agregados por
CEP. A diferença, se houver, fica em `conferencia_agregados_por_cep`.

## Testes

```bash
python -m unittest discover -s tests -t .
```

Os testes de unidade cobrem normalização, centroide, nível de
geocodificação e a CLI sobre uma base mínima. `tests/test_dataset.py`
lê a base gerada: totais, tamanho de cada JSON, o CEP da Avenida Paulista
e o da Praça da Sé (`01001-000`).

## Licenças deste repositório

- Código (`cepdb/`, testes, gerador): MIT, arquivo `LICENSE`.
- Dados em `data/`: domínio público do IBGE, com atribuição. Arquivo `NOTICE`.
