# Placar do Senado

O que cada um dos 81 senadores faz, só com dados oficiais: presença nas votações, propostas separadas em grupos
fáceis de entender, votos, com quem cada um vota junto, quanto custou o mandato, as emendas já pagas por setor,
uma nota de 0 a 10 com regra pública, o comparativo entre dois senadores e o Custódromo com o gasto do Senado no ano.
Um projeto do **Ranking da Direita**, irmão do [Placar da Câmara](https://mateuszani.github.io/rankingdadireitaplacardacamara/).

**Site:** https://mateuszani.github.io/rankingdadireitaplacardosenado/

## O robô

Todo dia às 03:47 (horário de Brasília) o GitHub roda `robo/rodar.py`, que:

1. baixa de novo, das fontes oficiais:
   - Dados Abertos Legislativos do Senado: senadores em exercício, períodos de exercício, votações nominais
     (com o voto ou a ausência de cada senador), orientação das lideranças e proposições de cada senador;
   - Dados Abertos Administrativos do Senado: cota parlamentar (CEAPS) e recursos usados por senador;
   - Transparência do Senado: execução orçamentária do ano (Custódromo);
   - Portal da Transparência (CGU): arquivo de emendas parlamentares (empenhado, liquidado e pago);
2. calcula tudo o que o site mostra;
3. **confere** antes de publicar. O site **não** é publicado se:
   - a lista oficial não tiver entre 78 e 81 senadores;
   - alguma fonte não puder ser lida (mandatos, votações, orientações, proposições, cota, recursos usados, emendas, gasto do Senado);
   - algum ano passado ficar sem votações, ou houver menos de 150 votações nominais;
   - alguma votação aberta vier sem a lista de votos completa;
   - as votações principais não baterem com o placar oficial do painel;
   - houver menos de 3.000 propostas;
   - menos de 75 senadores tiverem custo, ou a cota parlamentar não bater entre as duas fontes do Senado;
   - menos de 60 senadores tiverem emendas encontradas;
   - qualquer contagem cair mais de 1% em relação à última versão publicada;
4. só se tudo passar, monta o site em `saida/` e publica no GitHub Pages.

Se a conferência falhar, a rodada aparece com ❌ na aba **Actions**, com a lista dos problemas,
e o site continua com a última versão boa. Para rodar na hora: aba **Actions** → *Atualizar o Placar do Senado* → **Run workflow**
(desmarque *Publicar* para só testar: o resultado fica no artefato `teste`).

## Arquivos

- `robo/rodar.py`: coleta, cálculos, conferência e montagem do site.
- `robo/fontes.py`: acesso às fontes oficiais, com repetição em caso de falha.
- `robo/classificar.py`: regras de texto usadas para achar homenagens dentro de projetos de lei.
- `modelo/index.html`: o site, com marcadores que o robô preenche.
