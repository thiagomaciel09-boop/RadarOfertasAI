# RadarOfertas AI

## Descrição

O RadarOfertas AI monitora os preços de uma lista de produtos selecionados do Mercado Livre, guarda o histórico de cada preço e indica as melhores oportunidades do dia, sempre com o motivo da indicação.

As ofertas são divulgadas com links de afiliado, e cada publicação passa por **aprovação manual** antes de ser enviada.

## Como funciona

1. Coleta periódica dos preços pela API oficial do Mercado Livre.
2. Histórico de preços salvo localmente (SQLite).
3. Ranking diário: o preço atual é comparado ao histórico do próprio produto, para evitar descontos falsos.
4. Mensagem curta e honesta, com o link de afiliado, revisada antes de publicar.

## Objetivo

Ajudar as pessoas a encontrar boas ofertas reais, com curadoria e sem conteúdo aleatório.
