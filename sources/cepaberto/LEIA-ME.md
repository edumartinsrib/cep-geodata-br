# Dump do CEP Aberto

`cepaberto_2026-11-17.tar` é o dump oficial gerado em 17/11/2026, baixado
com sessão autenticada no [CEP Aberto](https://www.cepaberto.com/). O script
não consegue baixar outro sozinho: o site exige login.

O tar tem 137 ZIPs: `estados.cepaberto.zip`, `cidades.cepaberto.zip` e cinco
partes por UF (`<uf>.cepaberto_parte_<1-5>.zip`). Cada ZIP traz um CSV sem
cabeçalho e um `README.txt`.

Colunas, como publicadas pelo site:

- estados: `id`, `nome`, `sigla`
- cidades: `id`, `nome`, `estado_id`
- CEPs: `cep`, `logradouro`, `complemento`, `bairro`, `cidade_id`, `estado_id`

O próprio site informa que este dump **não traz geolocalização**.

Licença: [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1.0/).
Atribuição: Mucelli Rezende Oliveira, Eduardo, 2014, CEP Aberto
(https://www.cepaberto.com/).

O arquivo tem cerca de 13 MB e a licença permite redistribuir com atribuição
e compartilhamento pela mesma licença, então ele fica versionado neste
diretório. Para usar um dump mais novo, substitua o tar e rode:

```bash
python -m cepdb.cepaberto --tar sources/cepaberto/cepaberto_2026-11-17.tar
```

A base CNEFE em `data/` precisa já existir (`python -m cepdb.build`).
Se o tar padrão estiver no lugar, o build chama esta etapa no final.
