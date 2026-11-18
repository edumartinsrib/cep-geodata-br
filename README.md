# cepdb

Base local de CEPs do Brasil com latitude e longitude. Junta duas fontes
abertas:

- o [CNEFE do Censo Demográfico 2022](https://www.ibge.gov.br/estatisticas/sociais/populacao/38734-cadastro-nacional-de-enderecos-para-fins-estatisticos.html) (IBGE), que tem coordenada por endereço;
- o [CEP Aberto](https://www.cepaberto.com/), dump postal de 17/11/2026, que não tem coordenada e preenche logradouro, bairro e complemento.

Não é o Diretório Nacional de Endereços dos Correios e não enumera as
100 milhões de combinações teóricas de 8 dígitos.

## Cobertura desta geração

Gerada em 2026-11-18. Os mesmos números estão em [`data/meta.json`](data/meta.json).

- **1.217.509 CEPs** no total.
- **931.499** vêm do CNEFE (o conjunto inteiro do agregado oficial por CEP de 14/06/2024). Desses, 852.902 também estão no CEP Aberto e 78.597 só no CNEFE.
- **1.138.912** CEPs lidos do dump do CEP Aberto (o CSV tem 1.138.913 linhas; uma linha vazia foi ignorada). O site lista, por exemplo, 301.267 em SP e 116.328 em MG, e a contagem deste tar bate com isso. 286.010 existem só no CEP Aberto.
- **99,97%** têm latitude e longitude (312 ficam sem, porque o município do dump não foi reconhecido no IBGE).
- Precisão da coordenada:
  - `cnefe_cep`: 931.499 (o centroide do próprio CEP no censo)
  - `bairro`: 180.735
  - `municipio`: 83.457
  - `logradouro`: 21.506
  - sem coordenada: 312
- Nos CEPs que já existiam, o CEP Aberto preencheu 22.763 logradouros, 25.666 bairros e 128.690 complementos que estavam vazios. Nenhum município do IBGE estava vazio.
- Quando os nomes divergem de verdade (não é só acento ou caixa): 166.306 logradouros, 314.482 bairros e 936 cidades. O texto exibido do logradouro e do bairro passa a ser o do CEP Aberto; o do CNEFE fica em `divergencias`. A cidade oficial do IBGE não é trocada por uma localidade.
- 98 partições em `data/ceps/`. Não há CEPs começando com `00` nem com `10`. O maior arquivo é `ceps/13.json`, com 33,4 MB. A base ocupa cerca de 575 MB. O tar bruto do CEP Aberto ocupa 13 MB em `sources/cepaberto/`.

`01310-100` continua a Avenida Paulista, Bela Vista, São Paulo/SP, código
IBGE `3550308`, centroide **-23,565807, -46,651293** (`precisao: cnefe_cep`,
1.811 endereços). O complemento veio do CEP Aberto: "de 612 a 1510 - lado par".
O ponto fica no corredor da avenida, a cerca de 700 m do MASP.

`01310-900` só existe no CEP Aberto ("Avenida Paulista 52", Bela Vista).
A coordenada é o centroide dos CEPs do CNEFE com a mesma rua e o mesmo
bairro (`precisao: logradouro`), a 13 m do centroide de `01310-100`. Não é
a porta do número 52.

## Consulta

```bash
python -m cepdb 01310100
python -m cepdb 01310-100
python -m cepdb 01310900
python -m cepdb --near -23.5614 -46.6559 --radius-km 0.4 --limit 5
```

Na biblioteca:

```python
from cepdb.query import Database

db = Database()
print(db.lookup("01310-100"))
print(db.lookup("01310-900"))
print(db.near(-23.5614, -46.6559, radius_km=0.4, limit=5))
```

`lookup` devolve um dicionário ou `None`. A busca por proximidade acrescenta
`dist_km`. O diretório da base é `./data`, ou o caminho em `CEPDB_DATA` /
`--data-dir`.

Campos de cada CEP:

| Campo | Conteúdo |
| --- | --- |
| `cep` | 8 dígitos, sem hífen |
| `logradouro` | nome postal. Se CNEFE e CEP Aberto concordam, vale a grafia do CEP Aberto. Se divergem, também. Se só um tem nome, vale esse |
| `complemento` | texto do CEP Aberto, sem o prefixo `- ` que o dump usa |
| `bairro` | mesma regra do logradouro. No CNEFE a origem é a localidade censitária, não um campo de bairro |
| `cidade` | município do IBGE quando o CEP está no CNEFE ou quando o nome do dump casa com um município. Não é substituída por distrito |
| `uf` | sigla |
| `codigo_ibge` | 7 dígitos, ou `null` se o município não foi reconhecido |
| `lat`, `lng` | graus decimais, 6 casas, ou `null` |
| `precisao` | `cnefe_cep`, `logradouro`, `bairro`, `municipio` ou `null` |
| `fonte` | `ibge-cnefe-censo2022`, `cepaberto` ou `ibge-cnefe-censo2022+cepaberto` |
| `origem` | de onde veio cada campo de texto e a coordenada |
| `divergencias` | texto que ficou de fora, só quando os nomes não batem |
| `n_enderecos`, `nv_geo`, `n_logradouros` | só para CEP que está no CNEFE; senão `null` |

## Layout dos arquivos

```
data/meta.json          totais, licenças, método, hashes das partições
data/ceps/01.json       CEPs cujo prefixo é 01, objeto indexado pelo CEP
data/grid/-24_-47.json  grade de 1° só com cep, lat, lng (busca por proximidade)
sources/cepaberto/      tar do dump de 2026-11-17
```

As partições ficam abaixo de 50 MB (a maior tem 33 MB), em JSON sem
compressão. Não foi preciso Git LFS nem `.json.gz`.

## Fontes

### IBGE — CNEFE, Censo 2022

- Diretório: <https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/>
- Coleta: 2022. CSV publicado em 21/05/2024. Rondônia atualizada em 12/06/2024.
- 111.102.875 endereços. Nenhum CEP inválido e nenhuma coordenada rejeitada no recorte do Brasil.
- **99,84%** dos CEPs do CNEFE (929.975) usam coordenada original do endereço (`nv_geo` 1). Os demais: 760 no nível 2, 295 no 3, 447 no 4 e 22 no 6.
- Licença: domínio público, com citação da fonte (atendimento IBGE 8171/2009; Decreto nº 8.777/2016).

O agregado oficial por CEP (14/06/2024, 931.499 códigos, sem coordenada)
foi usado só para conferir o conjunto. O resultado está em
`conferencia_agregados_por_cep`.

### CEP Aberto

- Dump gerado em 17/11/2026, versionado em `sources/cepaberto/cepaberto_2026-11-17.tar`.
- O site exige login para baixar. O script não baixa outro dump sozinho.
- O README dentro de cada ZIP e o site dizem que o dump **não traz geolocalização**.
- 10.665 cidades no cadastro deles (município e distrito). 4.991 nomes trazem o município entre parênteses. Neste tar, só 8 CEPs usam esse formato; 1.137.692 casaram o nome exato de um município do CNEFE; 1.212 não casaram.
- Licença: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1.0/). Atribuição: Mucelli Rezende Oliveira, Eduardo, 2014. <https://www.cepaberto.com/>.
- O tar tem 13 MB. A ODbL permite redistribuir com atribuição e share-alike, então o bruto fica no repositório. Um dump mais novo substitui o arquivo e é passado com `--tar`.

A base combinada em `data/` passa a ser ODbL por causa dessa fonte. Ver `NOTICE`.

### Avaliadas e não usadas como geometria

| Fonte | Data | Licença | Por que ficou de fora |
| --- | --- | --- | --- |
| Agregados do CNEFE por CEP | 14/06/2024 | IBGE | Sem coordenada. Só auditoria do conjunto. |
| Coordenadas de endereços do IBGE | 02/02/2024 | IBGE | Substituídas pelo CSV de maio/junho de 2024. |
| [GeoNames BR.zip](https://download.geonames.org/export/zip/BR.zip) | 17/11/2026 | CC BY 4.0 | 5.525 registros, em geral um CEP por município. |
| [BrasilAPI](https://brasilapi.com.br/) | 18/11/2026 | sem dump | Consulta de um CEP por vez. |
| e-DNE dos Correios | — | licença da empresa | Não é dado aberto. Não foi copiado. |
| OpenAddresses / OpenStreetMap | — | em geral ODbL | Cobertura de CEP heterogênea diante do CNEFE. |

## Como a coordenada é calculada

Para CEP que está no CNEFE, a coordenada **não muda**: é a média de
latitude e de longitude dos endereços daquele CEP, só no melhor
`NV_GEO_COORD`. `precisao` fica `cnefe_cep`.

Níveis do IBGE, do mais preciso ao mais grosseiro: 1 endereço original,
2 apartamentos no mesmo número, 3 estimada, 4 face de quadra, 5 localidade,
6 setor censitário.

Para CEP que só está no CEP Aberto, nesta ordem:

1. centroide ponderado por `n_enderecos` dos CEPs do CNEFE no mesmo município, com o mesmo logradouro e o mesmo bairro (`precisao: logradouro`);
2. senão, o mesmo bairro no município (`bairro`);
3. senão, todos os CEPs do município (`municipio`);
4. sem município reconhecido, `lat`/`lng` ficam nulos.

Na comparação da rua, um número no fim do nome do CEP Aberto é ignorado
("Avenida Paulista 52" casa com "Avenida Paulista"). O texto gravado
continua o original. O peso é `n_enderecos`, então um CEP com mil
endereços puxa o centroide mais do que um CEP com dez.

Logradouro e bairro vindos só do CNEFE continuam sujeitos à regra dos 50%:
o nome modal só entra se cobre pelo menos metade dos endereços nomeados.
`SEM DENOMINACAO` não conta. O CEP Aberto preenche o vazio depois disso.

No `01001-000`, o CNEFE lançou 6.999 endereços espalhados por São Paulo
(latitude de -23,93 a -23,42), sem logradouro majoritário. A coordenada
permanece esse centroide, a cerca de 4 km da Praça da Sé. O nome postal
agora vem do CEP Aberto: Praça da Sé, Sé, lado ímpar. `origem.logradouro`
é `cepaberto` e `origem.coordenada` é `cnefe`.

## Limitações

- Ainda faltam CEPs que não estão nem no CNEFE nem neste dump.
- `precisao` `logradouro`, `bairro` ou `municipio` não é a porta daquele CEP. É o centro de outros CEPs do censo com o mesmo nome.
- `01310-900` ilustra isso: o número 52 não foi geocodificado; o ponto é o da Avenida Paulista na Bela Vista.
- O CNEFE não tem bairro postal. Onde os nomes divergem, o bairro exibido é o do CEP Aberto e o da localidade censitária fica em `divergencias`.
- A cidade do IBGE não é substituída quando o dump traz outro nome (936 casos). O outro nome fica em `divergencias.cidade`.
- `n_enderecos` conta linhas do microdado. A mesma coordenada repetida não muda o centroide.
- Grafia do CNEFE, quando é a única fonte do campo, permanece como o IBGE publicou.

## Regenerar

Python 3.11 ou superior. O CNEFE precisa de rede e de cerca de 4 GB para os ZIP (o CSV passa de 18 GB e é lido em fluxo). A etapa do CEP Aberto só precisa do tar local e de alguns GB de RAM.

```bash
python -m cepdb.build
python -m cepdb.cepaberto
```

`python -m cepdb.build` chama o cruzamento no final se
`sources/cepaberto/cepaberto_2026-11-17.tar` existir. Para um tar em outro
caminho:

```bash
python -m cepdb.cepaberto --tar /caminho/do/dump.tar
```

Os ZIP do IBGE vão para `raw/` e não entram no Git. `--only 35` regrava
`data/` só com aquela UF e não chama o CEP Aberto.

## Testes

```bash
python -m unittest discover -s tests -t .
```

Há testes da regra de texto, da herança de coordenada, da CLI e da base
gerada, inclusive `01310-100` e o CEP só do CEP Aberto `01310-900`.

## Licenças

- Código (`cepdb/`, testes): MIT, arquivo `LICENSE`.
- `sources/cepaberto/` e a base combinada em `data/`: ODbL 1.0. Atribuição ao CEP Aberto (Eduardo Mucelli Rezende Oliveira, 2014) e ao IBGE. Base derivada usada em público também sai sob ODbL.
- Os microdados do IBGE, isoladamente, são domínio público com citação. No arquivo combinado, a ODbL prevalece.

Detalhes no `NOTICE`.
