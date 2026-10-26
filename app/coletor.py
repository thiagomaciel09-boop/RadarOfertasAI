"""Coletor de precos do Mercado Livre -> SQLite.

Uso:
    python app/coletor.py                      # 10 mais vendidos de cada categoria + lista curada
    python app/coletor.py --por-categoria 5    # menos produtos por categoria
    python app/coletor.py --so-categoria MLB1051
    python app/coletor.py --sem-destaques      # so a lista curada

Lista curada (opcional): data/produtos_curados.txt, um codigo de produto de
catalogo por linha (ex.: MLB54982411). Linhas com # sao comentarios.

Cada execucao grava uma "coleta" e, para cada produto, as 3 ofertas mais baratas
de produto novo (rank_oferta 1 = a mais barata). O historico acumula a cada execucao: ele e o ativo
do projeto, a API nao fornece historico.
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml_auth import obter_access_token  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "radar.db"
CURADOS_PATH = RAIZ / "data" / "produtos_curados.txt"
API = "https://api.mercadolibre.com"
PAUSA = 0.3  # segundos entre consultas, para nao sobrecarregar a API
VENDEDOR_VALIDADE_S = 24 * 3600
OFERTAS_POR_PRODUTO = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS coletas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciada_em TEXT NOT NULL,
    finalizada_em TEXT,
    produtos_ok INTEGER DEFAULT 0,
    produtos_erro INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS categorias (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS produtos (
    id TEXT PRIMARY KEY,
    nome TEXT,
    permalink TEXT,
    categoria_id TEXT,
    curado INTEGER DEFAULT 0,
    primeira_vez TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS destaques (
    coleta_id INTEGER NOT NULL,
    categoria_id TEXT NOT NULL,
    produto_id TEXT NOT NULL,
    posicao INTEGER NOT NULL,
    PRIMARY KEY (coleta_id, categoria_id, produto_id)
);
CREATE TABLE IF NOT EXISTS precos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coleta_id INTEGER NOT NULL,
    produto_id TEXT NOT NULL,
    coletado_em TEXT NOT NULL,
    item_id TEXT,
    preco REAL NOT NULL,
    preco_original REAL,
    frete_gratis INTEGER,
    loja_oficial INTEGER,
    vendedor_id INTEGER,
    n_ofertas INTEGER,
    rank_oferta INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_precos_produto ON precos (produto_id, coletado_em);
CREATE TABLE IF NOT EXISTS vendedores (
    id INTEGER PRIMARY KEY,
    nickname TEXT,
    nivel TEXT,
    mercado_lider TEXT,
    vendas_concluidas INTEGER,
    avaliacoes_positivas REAL,
    atualizado_em TEXT NOT NULL
);
"""


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(caminho, params=None, tentativas=4):
    """GET com token atual e novas tentativas em erros passageiros.

    Devolve (status, json_ou_None). Nunca imprime o token.
    """
    for n in range(tentativas):
        try:
            r = requests.get(
                API + caminho,
                params=params,
                headers={"Authorization": "Bearer " + obter_access_token()},
                timeout=30,
            )
        except requests.RequestException:
            time.sleep(2 ** n)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** n)
            continue
        time.sleep(PAUSA)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    return 0, None


def ler_curados():
    if not CURADOS_PATH.exists():
        return []
    ids = []
    for linha in CURADOS_PATH.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if linha:
            ids.append(linha)
    return ids


def descobrir_produtos(db, coleta_id, por_categoria, so_categoria, usar_destaques):
    """Devolve {produto_id: (categoria_id, curado)} e grava os destaques."""
    alvo = {}
    for pid in ler_curados():
        alvo[pid] = (None, 1)

    if not usar_destaques:
        return alvo

    status, cats = get("/sites/MLB/categories")
    if status != 200:
        sys.exit(f"Nao consegui listar categorias (HTTP {status}).")
    for c in cats:
        db.execute("INSERT OR REPLACE INTO categorias (id, nome) VALUES (?, ?)", (c["id"], c["name"]))
    db.commit()

    for c in cats:
        if so_categoria and c["id"] != so_categoria:
            continue
        status, d = get(f"/highlights/MLB/category/{c['id']}")
        if status != 200:
            print(f"  sem destaques: {c['name']} (HTTP {status})")
            continue
        for item in d.get("content", [])[:por_categoria]:
            if item.get("type") != "PRODUCT":
                continue
            db.execute(
                "INSERT OR IGNORE INTO destaques VALUES (?, ?, ?, ?)",
                (coleta_id, c["id"], item["id"], item["position"]),
            )
            if item["id"] not in alvo:
                alvo[item["id"]] = (c["id"], 0)
    db.commit()
    return alvo


def garantir_produto(db, pid, categoria_id, curado):
    linha = db.execute("SELECT nome FROM produtos WHERE id = ?", (pid,)).fetchone()
    if linha and linha[0]:
        return True
    status, p = get(f"/products/{pid}")
    if status != 200 or not p:
        return False
    db.execute(
        "INSERT OR REPLACE INTO produtos (id, nome, permalink, categoria_id, curado, primeira_vez) "
        "VALUES (?, ?, ?, ?, ?, COALESCE((SELECT primeira_vez FROM produtos WHERE id = ?), ?))",
        (pid, p.get("name"), p.get("permalink"), categoria_id, curado, pid, agora()),
    )
    return True


def atualizar_vendedor(db, vendedor_id):
    linha = db.execute("SELECT atualizado_em FROM vendedores WHERE id = ?", (vendedor_id,)).fetchone()
    if linha:
        idade = (datetime.now(timezone.utc) - datetime.fromisoformat(linha[0])).total_seconds()
        if idade < VENDEDOR_VALIDADE_S:
            return
    status, u = get(f"/users/{vendedor_id}")
    if status != 200 or not u:
        return
    rep = u.get("seller_reputation") or {}
    trans = rep.get("transactions") or {}
    ratings = trans.get("ratings") or {}
    db.execute(
        "INSERT OR REPLACE INTO vendedores VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            vendedor_id,
            u.get("nickname"),
            rep.get("level_id"),
            rep.get("power_seller_status"),
            trans.get("completed") if trans.get("completed") is not None else trans.get("total"),
            ratings.get("positive"),
            agora(),
        ),
    )


def coletar_preco(db, coleta_id, pid):
    status, d = get(f"/products/{pid}/items")
    if status != 200 or not d:
        return False
    ofertas = [o for o in d.get("results", []) if o.get("condition") == "new" and o.get("price")]
    if not ofertas:
        return False
    # As OFERTAS_POR_PRODUTO mais baratas: a mais barata pode ser de vendedor
    # ruim, e o ranking precisa ter alternativas confiaveis.
    for rank, o in enumerate(sorted(ofertas, key=lambda o: o["price"])[:OFERTAS_POR_PRODUTO], 1):
        frete = (o.get("shipping") or {}).get("free_shipping")
        db.execute(
            "INSERT INTO precos (coleta_id, produto_id, coletado_em, item_id, preco, preco_original, "
            "frete_gratis, loja_oficial, vendedor_id, n_ofertas, rank_oferta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                coleta_id,
                pid,
                agora(),
                o.get("item_id"),
                o["price"],
                o.get("original_price"),
                1 if frete else 0,
                1 if o.get("official_store_id") else 0,
                o.get("seller_id"),
                len(ofertas),
                rank,
            ),
        )
        if o.get("seller_id"):
            atualizar_vendedor(db, o["seller_id"])
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--por-categoria", type=int, default=10)
    ap.add_argument("--so-categoria")
    ap.add_argument("--sem-destaques", action="store_true")
    args = ap.parse_args()

    DB_PATH.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    coleta_id = db.execute("INSERT INTO coletas (iniciada_em) VALUES (?)", (agora(),)).lastrowid
    db.commit()
    print(f"Coleta {coleta_id} iniciada.")

    alvo = descobrir_produtos(db, coleta_id, args.por_categoria, args.so_categoria, not args.sem_destaques)
    print(f"{len(alvo)} produtos para coletar.")

    ok = erro = 0
    for i, (pid, (cat, curado)) in enumerate(alvo.items(), 1):
        if garantir_produto(db, pid, cat, curado) and coletar_preco(db, coleta_id, pid):
            ok += 1
        else:
            erro += 1
        db.commit()
        if i % 25 == 0:
            print(f"  {i}/{len(alvo)} (ok={ok}, erro={erro})")

    db.execute(
        "UPDATE coletas SET finalizada_em = ?, produtos_ok = ?, produtos_erro = ? WHERE id = ?",
        (agora(), ok, erro, coleta_id),
    )
    db.commit()
    db.close()
    print(f"Coleta {coleta_id} concluida: {ok} precos gravados, {erro} sem preco.")
    # Falha clara se quase nada funcionou (ex.: token invalido), para o agendador avisar.
    if alvo and ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
