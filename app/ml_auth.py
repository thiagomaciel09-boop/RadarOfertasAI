"""Autorizacao OAuth do Mercado Livre (Authorization Code + PKCE).

Uso:
    python app/ml_auth.py login    # primeira autorizacao (abre o navegador)
    python app/ml_auth.py refresh  # renova o access token
    python app/ml_auth.py check    # mostra se o token esta valido (sem exibi-lo)

Os segredos ficam so no arquivo .env (que o Git ignora). Nada e impresso.
"""
import base64
import hashlib
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key
import os

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
# Margem de seguranca: renova se faltar menos que isso para expirar.
MARGEM_SEGUNDOS = 300


def _env(nome):
    valor = os.getenv(nome, "").strip()
    if not valor:
        sys.exit(f"Falta {nome} no arquivo .env (veja .env.example).")
    return valor


def _salvar_tokens(resposta):
    """Grava os tokens no .env. O refresh token e de uso unico: salvar sempre."""
    set_key(ENV_PATH, "MERCADO_LIVRE_ACCESS_TOKEN", resposta["access_token"], quote_mode="never")
    set_key(ENV_PATH, "MERCADO_LIVRE_REFRESH_TOKEN", resposta["refresh_token"], quote_mode="never")
    expira_em = int(time.time()) + int(resposta["expires_in"])
    set_key(ENV_PATH, "MERCADO_LIVRE_EXPIRES_AT", str(expira_em), quote_mode="never")


def _pedir_token(dados):
    r = requests.post(
        TOKEN_URL,
        data=dados,
        headers={"accept": "application/json"},
        timeout=30,
    )
    if r.status_code != 200:
        # Mostra so o motivo, nunca os dados enviados.
        try:
            motivo = r.json().get("message") or r.json().get("error")
        except ValueError:
            motivo = "resposta inesperada"
        sys.exit(f"O Mercado Livre recusou (HTTP {r.status_code}): {motivo}")
    return r.json()


def login():
    load_dotenv(ENV_PATH)
    client_id = _env("MERCADO_LIVRE_CLIENT_ID")
    client_secret = _env("MERCADO_LIVRE_CLIENT_SECRET")
    redirect_uri = _env("MERCADO_LIVRE_REDIRECT_URI")

    # PKCE: o verifier fica so na memoria deste script.
    verifier = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
        "state": state,
    })
    print("Vou abrir o navegador. Entre no Mercado Livre e clique em Autorizar.")
    print("Na pagina que aparecer, clique em Copiar e cole o texto aqui.\n")
    webbrowser.open(url)

    colado = input("Cole aqui o texto (code=...&state=...): ").strip()
    partes = urllib.parse.parse_qs(colado)
    code = partes.get("code", [""])[0]
    state_recebido = partes.get("state", [""])[0]

    if not secrets.compare_digest(state_recebido, state):
        sys.exit("O 'state' nao confere. Nada foi salvo. Tente de novo.")
    if not code:
        sys.exit("Nao encontrei o code no texto colado. Nada foi salvo.")

    resposta = _pedir_token({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })
    _salvar_tokens(resposta)
    print("Autorizado! Tokens salvos no .env (nao foram exibidos).")


def refresh():
    load_dotenv(ENV_PATH, override=True)
    resposta = _pedir_token({
        "grant_type": "refresh_token",
        "client_id": _env("MERCADO_LIVRE_CLIENT_ID"),
        "client_secret": _env("MERCADO_LIVRE_CLIENT_SECRET"),
        "refresh_token": _env("MERCADO_LIVRE_REFRESH_TOKEN"),
    })
    _salvar_tokens(resposta)
    print("Token renovado e salvo no .env.")


def obter_access_token():
    """Para o coletor: devolve um token valido, renovando se precisar."""
    load_dotenv(ENV_PATH, override=True)
    expira_em = int(os.getenv("MERCADO_LIVRE_EXPIRES_AT", "0") or 0)
    if time.time() > expira_em - MARGEM_SEGUNDOS:
        refresh()
        load_dotenv(ENV_PATH, override=True)
    return _env("MERCADO_LIVRE_ACCESS_TOKEN")


def check():
    load_dotenv(ENV_PATH, override=True)
    expira_em = int(os.getenv("MERCADO_LIVRE_EXPIRES_AT", "0") or 0)
    restante = expira_em - int(time.time())
    if not os.getenv("MERCADO_LIVRE_ACCESS_TOKEN"):
        print("Ainda nao ha token. Rode: python app/ml_auth.py login")
    elif restante > 0:
        print(f"Token valido por mais {restante // 60} minutos.")
    else:
        print("Token expirado. Rode: python app/ml_auth.py refresh")


if __name__ == "__main__":
    comandos = {"login": login, "refresh": refresh, "check": check}
    if len(sys.argv) != 2 or sys.argv[1] not in comandos:
        sys.exit("Uso: python app/ml_auth.py [login|refresh|check]")
    comandos[sys.argv[1]]()
