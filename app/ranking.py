"""Ranking diario das melhores ofertas, com o motivo de cada uma.

Uso:
    python app/ranking.py                 # as 10 melhores da ultima coleta
    python app/ranking.py --n 5
    python app/ranking.py --casa          # so Casa, Eletrodomesticos, Construcao, Ferramentas
    python app/ranking.py --categoria bebes --categoria beleza

So entram ofertas de vendedores confiaveis (nivel verde e vendas minimas).
A nota (0 a 100) soma sinais; os que dependem de historico so contam quando
ha dias de observacao suficientes. A confianca do ranking depende disso.
Nada aqui e promessa de economia: o historico e so o que o app observou.
"""
import argparse
import sqlite3
import statistics
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "radar.db"

JANELA_DIAS = 30
MIN_DIAS_HISTORICO = 7      # abaixo disso nao julgamos preco nem desconto anunciado
MIN_VENDAS = 50
NIVEIS_OK = ("4_", "5_")    # 4_light_green, 5_green


CATEGORIAS_CASA = ("casa", "eletrodomesticos", "construcao", "ferramentas")


def normalizar(texto):
    """Minusculas e sem acento, para comparar nomes de categoria."""
    sem_acento = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in sem_acento if unicodedata.category(c) != "Mn").lower()


NIVEIS = {"green": "verde", "light_green": "verde-claro", "yellow": "amarelo",
          "orange": "laranja", "red": "vermelho"}


def link_produto(pid, permalink):
    """A API devolve o permalink de produto de catalogo vazio; o endereco
    publico segue o formato /p/<codigo>. Confira abrindo um link antes de usar."""
    return permalink or f"https://www.mercadolivre.com.br/p/{pid}"


def nome_curto(nome, limite=70):
    return nome if len(nome) <= limite else nome[: limite - 1].rstrip() + "…"


def mensagens(r):
    """Textos prontos. O link de afiliado e colado a mao: o app nao o gera.

    So afirma o que o ranking verificou; avisos nao viram propaganda.
    """
    nome = nome_curto(r["nome"])
    fatos = [m for m in r["motivos"] if not m.startswith("#")]
    grupo = [f"{nome}", f"Preço hoje: {brl(r['preco'])}"]
    grupo += [f"• {m}" for m in fatos]
    if r["confianca"] == "baixa":
        grupo.append("(Ainda estou montando o histórico de preço deste produto.)")
    grupo += ["", "👉 {LINK_DE_AFILIADO}", "", "Link de afiliado. Preço e estoque podem mudar."]
    dm = [
        "Oi! Aqui está o link que você pediu:",
        f"{nome} — {brl(r['preco'])} hoje",
        "👉 {LINK_DE_AFILIADO}",
        "",
        "É link de afiliado e o preço pode mudar. "
        "Se quiser receber as próximas ofertas, entra no grupo: {LINK_DO_GRUPO}",
    ]
    return "\n".join(grupo), "\n".join(dm)


def brl(valor):
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def limitar(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def confiavel(nivel, vendas, loja_oficial):
    if not nivel or not nivel.startswith(NIVEIS_OK):
        return False
    return bool(loja_oficial) or (vendas or 0) >= MIN_VENDAS


def historico(db, produto_id, desde):
    """Menor preco confiavel por dia: [(dia, preco)], do mais antigo ao mais novo."""
    linhas = db.execute(
        """SELECT substr(x.coletado_em, 1, 10) AS dia, x.preco, v.nivel, v.vendas_concluidas, x.loja_oficial
           FROM precos x LEFT JOIN vendedores v ON v.id = x.vendedor_id
           WHERE x.produto_id = ? AND x.coletado_em >= ?""",
        (produto_id, desde),
    ).fetchall()
    por_dia = {}
    for dia, preco, nivel, vendas, loja in linhas:
        if confiavel(nivel, vendas, loja):
            por_dia[dia] = min(preco, por_dia.get(dia, preco))
    return sorted(por_dia.items())


def pontuar(db, coleta_id, produto):
    pid, nome, link, cat_nome = produto
    ofertas = db.execute(
        """SELECT x.preco, x.preco_original, x.frete_gratis, x.loja_oficial, x.item_id,
                  v.nickname, v.nivel, v.vendas_concluidas
           FROM precos x LEFT JOIN vendedores v ON v.id = x.vendedor_id
           WHERE x.coleta_id = ? AND x.produto_id = ? ORDER BY x.preco""",
        (coleta_id, pid),
    ).fetchall()
    boas = [o for o in ofertas if confiavel(o[6], o[7], o[3])]
    if not boas:
        return None
    preco, original, frete, loja, item_id, vendedor, nivel, vendas = boas[0]

    motivos, avisos, nota = [], [], 0.0

    # --- historico de preco (so de vendedores confiaveis) ---
    desde = (datetime.now(timezone.utc) - timedelta(days=JANELA_DIAS)).isoformat(timespec="seconds")
    serie = historico(db, pid, desde)
    dias = len(serie)
    precos_antes = [p for _, p in serie[:-1]]  # sem a coleta de hoje

    if dias >= MIN_DIAS_HISTORICO and precos_antes:
        mediana = statistics.median(precos_antes)
        minimo = min(precos_antes)
        abaixo = (mediana - preco) / mediana
        nota += limitar(abaixo / 0.20) * 40
        if abaixo >= 0.03:
            motivos.append(f"{abaixo:.0%} abaixo do preço típico dos últimos {dias} dias ({brl(mediana)})")
        elif abaixo <= -0.03:
            avisos.append(f"{-abaixo:.0%} acima do preço típico dos últimos {dias} dias ({brl(mediana)})")
        else:
            motivos.append(f"preço na faixa normal dos últimos {dias} dias ({brl(mediana)})")
        if preco <= minimo:
            nota += 10
            motivos.append(f"menor preço observado em {dias} dias")
    else:
        mediana = None
        avisos.append(f"histórico curto ({dias} dia{'s' if dias != 1 else ''} de observação): preço ainda não comparável")

    # --- queda desde a observacao anterior ---
    if len(serie) >= 2:
        anterior = serie[-2][1]
        queda = (anterior - preco) / anterior
        if queda >= 0.02:
            nota += limitar(queda / 0.15) * 20
            motivos.append(f"caiu {queda:.0%} desde a última observação ({brl(anterior)})")

    # --- desconto anunciado ("de/por"): so vale se o historico confirmar ---
    if original and original > preco:
        anunciado = (original - preco) / original
        if mediana is not None:
            if mediana >= original * 0.9:
                nota += limitar(anunciado / 0.30) * 10
                motivos.append(f"desconto anunciado de {anunciado:.0%} confere com o histórico")
            else:
                nota -= 10
                avisos.append(
                    f"'de {brl(original)}' não confere: o preço típico recente era {brl(mediana)}"
                )
        else:
            avisos.append(f"desconto anunciado de {anunciado:.0%} (não verificado)")

    # --- vendedor e frete ---
    nota += 8 if nivel.startswith("5_") else 5
    motivos.append(
        ("loja oficial, " if loja else "")
        + f"vendedor de reputação {NIVEIS.get(nivel.split('_', 1)[1], nivel)} com {vendas or 0:,} vendas".replace(",", ".")
    )
    if loja:
        nota += 4
    if (vendas or 0) >= 1000:
        nota += 3
    if frete:
        nota += 5
        motivos.append("frete grátis")

    # --- popularidade (posicao nos mais vendidos da categoria) ---
    pos = db.execute(
        "SELECT MIN(posicao) FROM destaques WHERE coleta_id = ? AND produto_id = ?", (coleta_id, pid)
    ).fetchone()[0]
    if pos:
        nota += limitar((11 - pos) / 10) * 7
        motivos.append(f"#{pos} mais vendido em {cat_nome}")

    confianca = "alta" if dias >= 14 else "média" if dias >= MIN_DIAS_HISTORICO else "baixa"
    return {
        "nota": max(0.0, min(100.0, nota)),
        "nome": nome, "link": link_produto(pid, link), "preco": preco, "original": original,
        "motivos": motivos, "avisos": avisos, "dias": dias, "confianca": confianca,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--casa", action="store_true", help="so categorias de casa")
    ap.add_argument("--categoria", action="append", default=[], help="parte do nome da categoria (repetivel)")
    args = ap.parse_args()
    filtros = [normalizar(c) for c in args.categoria] + (list(CATEGORIAS_CASA) if args.casa else [])
    sys.stdout.reconfigure(encoding="utf-8")

    db = sqlite3.connect(DB_PATH)
    coleta = db.execute(
        "SELECT id, iniciada_em FROM coletas WHERE finalizada_em IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not coleta:
        sys.exit("Nenhuma coleta concluida. Rode: python app/coletor.py")
    coleta_id, quando = coleta

    produtos = db.execute(
        """SELECT DISTINCT p.id, p.nome, p.permalink, COALESCE(c.nome, 'lista curada')
           FROM precos x JOIN produtos p ON p.id = x.produto_id
           LEFT JOIN categorias c ON c.id = p.categoria_id
           WHERE x.coleta_id = ?""",
        (coleta_id,),
    ).fetchall()

    if filtros:
        # A lista curada ('lista curada') sempre entra: voce a escolheu a mao.
        produtos = [
            p for p in produtos
            if p[3] == "lista curada" or any(f in normalizar(p[3]) for f in filtros)
        ]
    itens = [r for r in (pontuar(db, coleta_id, p) for p in produtos) if r]
    itens.sort(key=lambda r: r["nota"], reverse=True)
    top = itens[: args.n]

    linhas = [f"# Melhores oportunidades — coleta de {quando[:10]}", ""]
    linhas.append(
        f"{len(itens)} de {len(produtos)} produtos tinham oferta de vendedor confiável. "
        "Os preços mudam; confira no link antes de publicar."
    )
    for i, r in enumerate(top, 1):
        linhas += ["", f"## {i}. {r['nome']}  (nota {r['nota']:.0f}, confiança {r['confianca']})",
                   f"Preço: {brl(r['preco'])}" + (f"  (anunciado de {brl(r['original'])})" if r["original"] else ""),
                   f"Link do produto: {r['link']}"]
        linhas += [f"- {m}" for m in r["motivos"]]
        linhas += [f"- ATENÇÃO: {a}" for a in r["avisos"]]
        grupo, dm = mensagens(r)
        linhas += ["", "Mensagem para o grupo (troque {LINK_DE_AFILIADO} pelo seu link):", "```", grupo, "```",
                   "Resposta para quem comentou (DM):", "```", dm, "```"]
    texto = "\n".join(linhas) + "\n"

    saida = RAIZ / "data" / f"ranking_{quando[:10]}.md"
    saida.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"(salvo em {saida})")


if __name__ == "__main__":
    main()
