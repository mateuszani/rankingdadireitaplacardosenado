"""Acesso às fontes oficiais do Senado, com repetição em caso de falha.

Com SENADO_CACHE=<arquivo.json> o robô lê as respostas de um arquivo (url -> texto) em vez da internet;
serve para testar o site sem baixar tudo de novo."""
import json
import os
import time

import requests

S = requests.Session()
S.headers.update({"User-Agent": "PlacarDoSenado/1.0 (+https://github.com/mateuszani/rankingdadireitaplacardosenado)",
                  "Accept": "application/json"})
CACHE = None
if os.environ.get("SENADO_CACHE"):
    CACHE = json.load(open(os.environ["SENADO_CACHE"]))

LEGIS = "https://legis.senado.leg.br/dadosabertos"
ADM = "https://adm.senado.gov.br/adm-dadosabertos/api/v1"


def texto(url, tentativas=6, minimo=2):
    if CACHE is not None:
        return CACHE.get(url)
    for k in range(tentativas):
        try:
            r = S.get(url, timeout=120)
            if r.status_code == 200 and len(r.content) >= minimo:
                r.encoding = r.encoding or "utf-8"
                return r.text
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(2 + 3 * k)
    return None


def js(url, **kw):
    t = texto(url, **kw)
    if t is None:
        return None
    return json.loads(t)


def arquivo(url, destino, tentativas=4):
    """Baixa um arquivo grande inteiro, conferindo o tamanho anunciado pelo servidor."""
    if CACHE is not None and os.path.exists(destino):
        return destino
    for k in range(tentativas):
        try:
            with S.get(url, stream=True, timeout=300, headers={"Accept": "*/*"}) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                n = 0
                with open(destino, "wb") as f:
                    for b in r.iter_content(1 << 20):
                        f.write(b)
                        n += len(b)
                if total and n != total:
                    raise IOError(f"{n} de {total} bytes")
                return destino
        except Exception as e:
            print(f"  tentativa {k + 1}: {e}", flush=True)
            time.sleep(5 + 10 * k)
    return None
