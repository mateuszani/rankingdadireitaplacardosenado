"""Robô do Radar NasRuas Senado: coleta os dados oficiais, calcula, confere e monta o site.

Fontes (todas oficiais):
- Dados Abertos Legislativos do Senado (legis.senado.leg.br/dadosabertos): senadores, mandatos, votações nominais,
  orientação das lideranças, proposições de autoria de cada senador.
- Dados Abertos Administrativos do Senado (adm.senado.gov.br): cota parlamentar (CEAPS) e recursos usados por senador.
- Transparência do Senado (www6g.senado.leg.br): execução orçamentária do Senado no ano.
- Portal da Transparência (CGU): emendas parlamentares (empenhado, liquidado e pago).
"""
import argparse
import bisect
import collections
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontes  # noqa: E402
from classificar import H_LEI, norm  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANOS = [2023, 2024, 2025, 2026]
ANOS_EMENDAS = [2024, 2025, 2026]  # Orçamentos votados nesta legislatura
SITE_URL = "https://mateuszani.github.io/rankingdadireitaplacardosenado/"
CENTRO = ["MDB", "PSD", "PP", "UNIÃO", "REPUBLICANOS"]
INICIO = "2023-02-01"
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)


def cortar(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 3].rsplit(" ", 1)[0] + "…"


def lista(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# ------------------------------------------------------------------ subsídio (Decreto Legislativo 172/2022)
def subsidio(ano, mes):
    if (ano, mes) < (2023, 4):
        return 39293.32
    if (ano, mes) < (2024, 2):
        return 41650.92
    if (ano, mes) < (2025, 2):
        return 44008.52
    return 46366.19


# ------------------------------------------------------------------ coleta
def coletar_senadores():
    j = fontes.js(f"{fontes.LEGIS}/senador/lista/atual.json")
    out = []
    for p in lista(j["ListaParlamentarEmExercicio"]["Parlamentares"]["Parlamentar"]):
        i = p["IdentificacaoParlamentar"]
        out.append({"id": int(i["CodigoParlamentar"]), "nome": i["NomeParlamentar"], "completo": i.get("NomeCompletoParlamentar", ""),
                    "partido": i.get("SiglaPartidoParlamentar") or "", "uf": i.get("UfParlamentar") or "",
                    "sexo": i.get("SexoParlamentar", ""), "part": p.get("Mandato", {}).get("DescricaoParticipacao", "")})
    return sorted(out, key=lambda d: norm(d["nome"]))


def coletar_mandatos(ids):
    """Períodos em exercício de cada senador (para contar os meses de salário)."""
    res = {}
    for i in ids:
        j = fontes.js(f"{fontes.LEGIS}/senador/{i}/mandatos.json")
        if j is None:
            res[i] = None
            continue
        per = []
        for m in lista(j["MandatoParlamentar"]["Parlamentar"].get("Mandatos", {}).get("Mandato")):
            for e in lista((m.get("Exercicios") or {}).get("Exercicio")):
                per.append([e.get("DataInicio"), e.get("DataFim")])
        res[i] = per
    return res


def coletar_votacoes(hoje):
    V = {}
    for a in ANOS:
        ini = f"{a}-01-01" if a > 2023 else INICIO
        fim = min(f"{a}-12-31", hoje.strftime("%Y-%m-%d"))
        j = fontes.js(f"{fontes.LEGIS}/votacao?dataInicio={ini}&dataFim={fim}")
        V[a] = j
    O = {}
    for a in ANOS:
        ini = (f"{a}0101" if a > 2023 else INICIO.replace("-", ""))
        fim = min(f"{a}1231", hoje.strftime("%Y%m%d"))
        O[a] = fontes.js(f"{fontes.LEGIS}/plenario/votacao/orientacaoBancada/{ini}/{fim}.json")
    return V, O


def coletar_processos(ids, hoje):
    res = {}
    for i in ids:
        res[i] = fontes.js(f"{fontes.LEGIS}/processo?codigoParlamentarAutor={i}&dataInicioApresentacao={INICIO}"
                           f"&dataFimApresentacao={hoje.strftime('%Y-%m-%d')}")
    return res


def coletar_custos(ids):
    CE = {}
    for a in ANOS:
        CE[a] = fontes.js(f"{fontes.ADM}/senadores/despesas_ceaps/{a}", minimo=100)
    RU = {}
    for i in ids:
        for a in ANOS:
            RU[(i, a)] = fontes.js(f"{fontes.ADM}/senadores/{i}/recursos-utilizados?ano={a}")
    return CE, RU


RE_TOTAL = re.compile(r"Total\s*R\$\s*([\d.,]+)\s*R\$\s*([\d.,]+)\s*R\$\s*([\d.,]+)\s*R\$\s*([\d.,]+)")
RE_CARGA = re.compile(r"Carga de dados em:\s*(\d{2}/\d{2}/\d{4})")


def num(s):
    return float(str(s).replace(".", "").replace(",", "."))


def coletar_gasto_senado(hoje):
    a = hoje.year
    url = (f"https://www6g.senado.leg.br/transparencia/orcfin/execucao-orcamentaria/resultado-execucao-orcamentaria"
           f"?ano={a}&mesInicio=1&mesFim=12&catEcon=&gnd=&elementoDespesa=")
    h = fontes.texto(url, minimo=5000)
    if not h:
        return None
    t = re.sub(r"<[^>]+>", " ", h)
    t = re.sub(r"\s+", " ", t.replace("&nbsp;", " "))
    m = RE_TOTAL.search(t)
    c = RE_CARGA.search(t)
    if not m:
        return None
    emp, liq, pago, rp = (num(x) for x in m.groups())
    return {"ano": a, "pago": round(pago + rp, 2), "pago_ano": pago, "rp_pago": rp, "empenhado": emp,
            "carga": c.group(1) if c else None}


URL_EMENDAS = "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares/UNICO"


def coletar_emendas(dirdados):
    os.makedirs(dirdados, exist_ok=True)
    z = os.path.join(dirdados, "emendas.zip")
    if not fontes.arquivo(URL_EMENDAS, z):
        return None
    with zipfile.ZipFile(z) as f:
        nome = [n for n in f.namelist() if n.lower() == "emendasparlamentares.csv"][0]
        d = pd.read_csv(io.BytesIO(f.read(nome)), sep=";", encoding="latin1", dtype=str)
    return d


# ------------------------------------------------------------------ votações
PRESENTE = {"Sim", "Não", "Abstenção", "P-NRV", "Votou", "Obstrução"}
JUSTIF = {"AP", "LS", "LP", "MIS", "LAP", "LC", "LG", "LAA", "LA"}


def tipo_voto(c):
    c = c or ""
    if c in PRESENTE or c.startswith("Presidente"):
        return "p"
    if c == "NCom":
        return "an"
    if c in JUSTIF or (c[:1] == "L" and len(c) <= 4):
        return "aj"
    return None  # NA e outros: não conta


def pos_of(s, n, o=0):
    if s > n + o:
        return "S"
    if n + o > s:
        return "N"
    return None


def carregar_posicoes():
    try:
        c = json.load(open(os.path.join(RAIZ, "robo", "posicoes_nasruas.json"), encoding="utf-8"))
    except FileNotFoundError:
        c = {}
    c.setdefault("ativa", False); c.setdefault("minimo_votacoes", 10); c.setdefault("votacoes", {})
    c.setdefault("pesos", {"alinhamento": 0.60, "custo": 0.20, "merito": 0.15, "presenca": 0.05})
    return c


def nota_nasruas(NOTA, ALIN, cfg):
    """Nota Nas Ruas: alinhamento (0-10 pela % de votos iguais à posição do movimento) + custo, mérito e presença
    (as mesmas notas de 0 a 10 por posição entre os colegas). Devolve {id: [nota, alinhamento%, n_votacoes]}."""
    p = cfg["pesos"]; out = {}
    for r in NOTA:
        i, sp, sv_, sl, sc_ = r[0], r[2], r[3], r[4], r[5]
        a = ALIN.get(i)
        if not a or a[1] < cfg["minimo_votacoes"]:
            out[i] = [None, a[0] if a else None, a[1] if a else 0]
            continue
        pres = [x for x in (sp, sv_) if x is not None]
        partes = [(p["alinhamento"], a[0] / 10), (p["custo"], sc_), (p["merito"], sl), (p["presenca"], sum(pres) / len(pres) if pres else None)]
        partes = [(w, x) for w, x in partes if x is not None]
        out[i] = [round(sum(w * x for w, x in partes) / sum(w for w, _ in partes), 1), a[0], a[1]]
    return out


def calcular_votacoes(V, O, sens):
    vots = []
    vistos = set()
    for a in ANOS:
        for v in (V.get(a) or []):
            if (v.get("dataSessao") or "") < INICIO:
                continue
            k = f"{v.get('codigoSessaoVotacao')}-{v.get('sequencialVotacao')}"
            if k in vistos:
                continue
            vistos.add(k)
            vots.append(v)
    vots.sort(key=lambda v: (v["dataSessao"], v.get("sequencialVotacao") or 0))
    ids = [f"{v.get('codigoSessaoVotacao')}-{v.get('sequencialVotacao')}" for v in vots]
    data = {i: v["dataSessao"] for i, v in zip(ids, vots)}
    secreta = {i: v.get("votacaoSecreta") == "S" for i, v in zip(ids, vots)}
    # orientação do Governo e placar oficial (painel)
    ORI = {}
    for a in ANOS:
        for o in ((O.get(a) or {}).get("votacoes") or []):
            ORI[(str(o.get("dataInicioVotacao", ""))[:10], o.get("siglaTipoMateria"), o.get("numeroMateria"), o.get("anoMateria"),
                 o.get("qtdVotosSim"), o.get("qtdVotosNao"))] = o
    POS = collections.defaultdict(dict)
    VOTOS = {}
    sim_nao = {}
    for i, v in zip(ids, vots):
        cnt = collections.defaultdict(lambda: [0, 0, 0])
        s = n = 0
        vs = lista(v.get("votos"))
        VOTOS[i] = vs
        for x in vs:
            c = x.get("siglaVotoParlamentar")
            p = x.get("siglaPartidoParlamentar") or ""
            if c == "Sim":
                cnt[p][0] += 1; s += 1
            elif c == "Não":
                cnt[p][1] += 1; n += 1
        sim_nao[i] = (s, n)
        if not secreta[i]:
            POS[i] = {p: tuple(c) for p, c in cnt.items()}
    GOV = {}
    for i, v in zip(ids, vots):
        if secreta[i]:
            continue
        o = ORI.get((data[i], v.get("sigla"), int(v["numero"]) if str(v.get("numero", "")).isdigit() else v.get("numero"),
                     v.get("ano"), sim_nao[i][0], sim_nao[i][1]))
        if o:
            g = [x for x in lista(o.get("orientacoesLideranca")) if (x.get("partido") or "").lower() == "governo"]
            if g and g[0].get("voto") in ("SIM", "NÃO", "NAO"):
                GOV[i] = "S" if g[0]["voto"] == "SIM" else "N"
    abertas = [i for i in ids if not secreta[i]]

    def dividida(i):
        s, n = sim_nao[i]
        t = s + n
        return t > 0 and min(s, n) / t >= 0.2

    DIV = {i: dividida(i) for i in abertas}

    def calc(a, bs, filtro=None):
        tog = tot = 0
        for i in abertas:
            if filtro and not filtro(i):
                continue
            m = POS[i]
            if a not in m:
                continue
            pa = pos_of(*m[a])
            agg = [0, 0, 0]
            for b in bs:
                if b in m:
                    agg = [x + y for x, y in zip(agg, m[b])]
            pb = pos_of(*agg)
            if not pa or not pb:
                continue
            tot += 1
            tog += pa == pb
        return [tog, tot]

    R = {}
    for A in ["PL", "NOVO"]:
        R[A] = {}
        for lab, B in [("PT", ["PT"]), ("CENTRO", CENTRO)]:
            d = {"todas": calc(A, B), "divididas": calc(A, B, lambda i: DIV[i])}
            for y in ANOS:
                d[str(y)] = calc(A, B, lambda i, y=y: data[i].startswith(str(y)))
            R[A][lab] = d
    R["porCentro"] = {A: {c: calc(A, [c]) for c in CENTRO} for A in ["PL", "NOVO"]}
    R["porCentroDiv"] = {A: {c: calc(A, [c], lambda i: DIV[i]) for c in CENTRO} for A in ["PL", "NOVO"]}
    porano = collections.Counter(data[i][:4] for i in ids)
    porano_ab = collections.Counter(data[i][:4] for i in abertas)
    R["meta"] = {"votacoes": len(ids), "abertas": len(abertas), "porAno": {str(y): porano.get(str(y), 0) for y in ANOS},
                 "abertasPorAno": {str(y): porano_ab.get(str(y), 0) for y in ANOS},
                 "divididas": sum(DIV.values()), "ultimaVotacao": max(data.values()) if data else ""}
    partidos = collections.Counter(p for i in abertas for p in POS[i])
    P = [p for p, n in partidos.most_common() if n >= 20]
    M = [[None if a == b else calc(a, [b]) for b in P] for a in P]
    G = {}
    for a in P:
        t = g = 0
        for i in abertas:
            if i in GOV and a in POS[i]:
                p = pos_of(*POS[i][a])
                if p:
                    t += 1; g += p == GOV[i]
        G[a] = [g, t]
    MX = {"P": P, "M": M, "G": G}
    # por senador
    sids = {d["id"] for d in sens}
    st = {}
    OFFv = collections.defaultdict(lambda: [0, 0, 0])  # (id, ano) -> presente, justificada, sem justificativa
    for i in ids:
        a = int(data[i][:4])
        for x in VOTOS[i]:
            sid = int(x.get("codigoParlamentar") or 0)
            if sid not in sids:
                continue
            c = x.get("siglaVotoParlamentar") or ""
            tv = tipo_voto(c)
            if tv is None:
                continue
            OFFv[(sid, a)][{"p": 0, "aj": 1, "an": 2}[tv]] += 1
            s = st.setdefault(sid, {"n": 0, "tot": 0, "S": 0, "N": 0, "A": 0, "vy": collections.Counter(),
                                    "part": 0, "partT": 0, "pt": 0, "ptT": 0, "pl": 0, "plT": 0, "gov": 0, "govT": 0})
            s["tot"] += 1
            votou = c in ("Sim", "Não", "Abstenção", "Votou")
            if votou:
                s["n"] += 1; s["vy"][a] += 1
            k = "S" if c == "Sim" else "N" if c == "Não" else "A"
            s[k] += 1
            mine = "S" if c == "Sim" else "N" if c == "Não" else None
            if not mine or secreta[i]:
                continue
            m = POS[i]
            pp_ = x.get("siglaPartidoParlamentar") or ""
            cc = m.get(pp_)
            if cc:
                o = (cc[0] - (c == "Sim"), cc[1] - (c == "Não"), 0)
                pp = pos_of(*o)
                if pp:
                    s["partT"] += 1; s["part"] += pp == mine
            if pp_ != "PT" and "PT" in m:
                pp = pos_of(*m["PT"])
                if pp:
                    s["ptT"] += 1; s["pt"] += pp == mine
            if pp_ != "PL" and "PL" in m:
                pp = pos_of(*m["PL"])
                if pp:
                    s["plT"] += 1; s["pl"] += pp == mine
            if i in GOV:
                s["govT"] += 1; s["gov"] += GOV[i] == mine
    # alinhamento com a posição do Nas Ruas (robo/posicoes_nasruas.json); faltar, abster ou obstruir conta como não alinhado
    NRC = carregar_posicoes()
    ALIN = {}
    marc = {k: v for k, v in NRC["votacoes"].items() if k in data and not secreta[k] and v.get("posicao") in ("S", "N")}
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for k, v in marc.items():
        w = float(v.get("peso") or 1)
        for x in VOTOS[k]:
            sid = int(x.get("codigoParlamentar") or 0)
            c = x.get("siglaVotoParlamentar") or ""
            if sid not in sids or tipo_voto(c) is None:
                continue  # fora do exercício naquela votação
            meu = "S" if c == "Sim" else "N" if c == "Não" else None
            acc[sid][0] += w * (meu == v["posicao"]); acc[sid][1] += w; acc[sid][2] += 1
    for sid, (ok, tot, n) in acc.items():
        if n:
            ALIN[sid] = [round(100 * ok / tot, 1), n]
    pc = lambda a, b: round(100 * a / b, 1) if b else ""
    DEPS, VYv = [], []
    for d in sens:
        s = st.get(d["id"])
        if not s:
            DEPS.append([d["id"], d["nome"], d["partido"], d["uf"], 0, 0, 0, 0, 0, 0, "", "", "", ""])
            VYv.append([d["id"], 0, 0, 0, 0])
            continue
        DEPS.append([d["id"], d["nome"], d["partido"], d["uf"], s["n"], s["tot"], s["S"], s["N"], 0, s["A"],
                     pc(s["part"], s["partT"]), pc(s["pt"], s["ptT"]), pc(s["pl"], s["plT"]), pc(s["gov"], s["govT"])])
        VYv.append([d["id"]] + [s["vy"].get(y, 0) for y in ANOS])
    VY = {"tot": [porano.get(str(y), 0) for y in ANOS], "v": VYv}
    # votações principais que dividiram o plenário (2025 em diante, votação aberta, projeto/PEC/MP)
    sel = []
    rx_ok = re.compile(r"^(PL|PLP|PEC|PDL|MPV|PLN|PLV)\b")
    rx_no = re.compile(r"(Requerimento|Destaque|destacad|Emenda n|Emendas n|Recurso|Parecer n|Veto)", re.I)
    by_id = dict(zip(ids, vots))
    for i in sorted(abertas, key=lambda i: data[i], reverse=True):
        v = by_id[i]
        ident = v.get("identificacao") or ""
        desc = v.get("descricaoVotacao") or ""
        if data[i] < "2025-01-01" or not DIV[i] or not rx_ok.match(ident) or rx_no.search(desc):
            continue
        s, n = sim_nao[i]
        sel.append([i, data[i], ident.split(" (")[0], int(v.get("codigoMateria") or 0), s, n,
                    cortar(v.get("ementa") or desc, 220), cortar(desc, 160)])
    sel = sel[:24]
    placar = []
    for v in sel:
        vv = by_id[v[0]]
        o = ORI.get((v[1], vv.get("sigla"), int(vv["numero"]) if str(vv.get("numero", "")).isdigit() else vv.get("numero"),
                     vv.get("ano"), v[4], v[5]))
        placar.append((v[0], bool(o), (v[4], v[5])))
    idx = {v[0]: k for k, v in enumerate(sel)}
    cod = {}
    for i in idx:
        for x in VOTOS[i]:
            sid = int(x.get("codigoParlamentar") or 0)
            if sid in sids:
                a_ = cod.setdefault(sid, ["-"] * len(sel))
                c = x.get("siglaVotoParlamentar") or ""
                a_[idx[i]] = "S" if c == "Sim" else "N" if c == "Não" else "A" if c == "Abstenção" else "P" if c.startswith("Presidente") else "-"
    COD = [[d["id"], "".join(cod.get(d["id"], ["-"] * len(sel)))] for d in sens]
    # últimas 20 votações abertas do plenário de cada senador: [data, descrição, voto, sim, não]
    ULT = {}
    for i in sorted(abertas, key=lambda i: data[i], reverse=True):
        v = by_id[i]
        ident = (v.get("identificacao") or "").split(" (")[0]
        desc = cortar((ident + " – " if ident else "") + (v.get("descricaoVotacao") or v.get("ementa") or ""), 300)
        for x in VOTOS[i]:
            sid = int(x.get("codigoParlamentar") or 0)
            if sid in sids and len(ULT.setdefault(sid, [])) < 20:
                ULT[sid].append([data[i][:10], desc, x.get("siglaVotoParlamentar") or "", sim_nao[i][0], sim_nao[i][1]])
    stats = {"votacoes": len(ids), "abertas": len(abertas), "por_ano": R["meta"]["porAno"], "placar": placar,
             "votos": sum(len(VOTOS[i]) for i in ids), "gov": len(GOV), "sem_votos": [i for i in abertas if len(VOTOS[i]) < 60], "ult": ULT, "alin": ALIN, "nr_marcadas": len(NRC["votacoes"])}
    return R, MX, DEPS, VY, sel, COD, OFFv, stats


# ------------------------------------------------------------------ propostas
LEIS = {"Projeto de Lei Ordinária", "Projeto de Lei Complementar", "Proposta de Emenda à Constituição",
        "Projeto de Decreto Legislativo", "Projeto de Resolução", "Projeto de Lei de Conversão"}
SIG = {"Projeto de Lei Ordinária": "PL", "Projeto de Lei Complementar": "PLP", "Proposta de Emenda à Constituição": "PEC",
       "Projeto de Decreto Legislativo": "PDL", "Projeto de Resolução": "PRS"}


def grupo(tipo_doc, conteudo, ementa):
    t = (conteudo or "").strip()
    e = norm(ementa)
    tl = norm(t)
    if tipo_doc in LEIS:
        if H_LEI.search(e):
            if re.search(r"\bdia (nacional|mundial|internacional|estadual|municipal)\b|semana nacional|mes nacional|ano nacional|data comemorativa|calendario oficial|calendario de datas", e) and not re.search(r"^\W*(denomina|da (o )?nome)", e):
                return "H", "Datas comemorativas"
            if re.search(r"^\W*(denomina|da (o )?nome)|altera a denominacao", e):
                return "H", "Nomes de rodovias, obras e lugares"
            return "H", "Títulos, prêmios, heróis e patrimônio cultural"
        return "L", None
    if tipo_doc.startswith("Indicação"):
        return "M", "Indicações (sugestões ao governo e a outros órgãos)"
    if re.search(r"sessao especial|comemoracao|sessao solene|congratula|aplauso|solidariedade|homenagem|pesar|louvor", tl) and "censura" not in tl:
        if "pesar" in tl:
            return "H", "Votos de pesar"
        if "sessao" in tl or "comemora" in tl:
            return "H", "Sessões especiais e comemorações"
        return "H", "Votos de aplauso, congratulações e solidariedade"
    if re.search(r"^informac|pedido de informac", tl):
        return "P", "Pedidos de informação a ministérios e órgãos"
    if re.search(r"convocacao|convite a autoridade|convite a cidadao", tl):
        return "P", "Convocação e convite de ministros e autoridades"
    if re.search(r"comissao parlamentar de inquerito|cpi", tl):
        return "P", "CPIs (investigações)"
    if re.search(r"fiscaliza|diligencia|representacao contra|tribunal de contas|tcu", tl):
        return "P", "Fiscalização"
    if re.search(r"audiencia publica|debates tematicos|sessao de debates", tl):
        return "O", "Audiências públicas, debates e eventos"
    if re.search(r"censura|repudio", tl):
        return "O", "Votos de censura e repúdio"
    if tipo_doc == "Emenda":
        return "O", "Mudanças em projetos (emendas)"
    if re.search(r"urgencia|adiamento|retirada|desarquivamento|tramitacao|calendario especial|destaque|inclusao em ordem|apensa|aditamento|dispensa", tl):
        return "O", "Pedidos sobre a pauta e o andamento das votações"
    if re.search(r"missao", tl):
        return "O", "Missões e viagens oficiais"
    return "O", "Demais"


def etapa(sit, tipo_doc):
    s = (sit or "").upper()
    if "NORMA JURÍDICA" in s or "NORMA JURIDICA" in s:
        return 1
    if s in ("REMETIDA À CÂMARA DOS DEPUTADOS", "APROVADA", "REMETIDA À SANÇÃO", "AGUARDANDO SANÇÃO", "ENVIADA À SANÇÃO",
             "REMETIDA À PROMULGAÇÃO") or "SANÇÃO" in s or "PROMULGA" in s:
        return 2
    if "PRONTA PARA" in s or "PRONTO PARA" in s or "INCLUÍDA NA PAUTA" in s or "INCLUIDA NA PAUTA" in s:
        return 3
    if any(x in s for x in ("ARQUIVADA", "PREJUDICADA", "RETIRADA", "REJEITADA", "TRAMITAÇÃO ENCERRADA", "IMPUGNADA")):
        return 5
    return 4


UF = {'Acre': 'AC', 'Alagoas': 'AL', 'Amapá': 'AP', 'Amazonas': 'AM', 'Bahia': 'BA', 'Ceará': 'CE', 'Distrito Federal': 'DF',
      'Espírito Santo': 'ES', 'Goiás': 'GO', 'Maranhão': 'MA', 'Mato Grosso do Sul': 'MS', 'Mato Grosso': 'MT', 'Minas Gerais': 'MG',
      'Pará': 'PA', 'Paraíba': 'PB', 'Paraná': 'PR', 'Pernambuco': 'PE', 'Piauí': 'PI', 'Rio de Janeiro': 'RJ',
      'Rio Grande do Norte': 'RN', 'Rio Grande do Sul': 'RS', 'Rondônia': 'RO', 'Roraima': 'RR', 'Santa Catarina': 'SC',
      'São Paulo': 'SP', 'Sergipe': 'SE', 'Tocantins': 'TO'}
RX_UF = re.compile(r"(?<![\wÀ-ú])(" + "|".join(re.escape(n) for n in sorted(UF, key=len, reverse=True)) + r")(?![\wÀ-ú])")
RX_UF2 = re.compile(r"[/\-–(]\s?(" + "|".join(UF.values()) + r")\b")
GEN = re.compile(r"^(altera|acrescenta|modifica|revoga|da nova redacao)( a| o| os| as)? (redacao (d[oa] )?)?(lei|decreto|artigo|art|inciso|paragrafo|dispositivo|dispositivos|codigo|constituicao)\b")
VAGO = re.compile(r"que especifica|ora formulad|em anexo|conforme (o )?anexo|abaixo|a seguir")


def ufs(t):
    t = str(t or "")
    return sorted({UF[x] for x in RX_UF.findall(t)} | set(RX_UF2.findall(t)))


def chave(s):
    s = norm(s)
    s = re.sub(r",? e da outras providencias.*$", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def autores(autoria, nomes_idx):
    """Lista de ids de senadores atuais na ordem da autoria oficial (o primeiro é o autor principal)."""
    out = []
    for parte in re.split(r",\s*(?=Senador|Senadora|Comiss|Deputad|Presid|Mesa|Lideran)", autoria or ""):
        m = re.match(r"\s*Senador[a]?\s+(.+?)\s*\(", parte)
        if m:
            i = nomes_idx.get(norm(m.group(1)))
            out.append(i if i else 0)
        else:
            out.append(0)
    return out


def calcular_propostas(PR, sens, saida):
    nomes_idx = {norm(d["nome"]): d["id"] for d in sens}
    todas = {}
    autoria = {}
    for sid, lst in PR.items():
        for p in (lst or []):
            k = int(p.get("codigoMateria") or p.get("id"))
            todas[k] = p
            autoria[k] = autores(p.get("autoria"), nomes_idx)
    rows = []
    for k, p in todas.items():
        td = p.get("tipoDocumento") or ""
        g, sub = grupo(td, p.get("tipoConteudo"), p.get("ementa"))
        rows.append({"id": k, "lab": (p.get("identificacao") or "").strip(), "dt": (p.get("dataApresentacao") or "")[:10], "g": g, "sub": sub,
                     "et": etapa(p.get("situacaoAtual"), td) if g == "L" else 0, "em": p.get("ementa") or "", "td": td,
                     "sig": (p.get("identificacao") or "").split(" ")[0], "sit": p.get("situacaoAtual") or "", "conteudo": p.get("tipoConteudo") or ""})
    P = pd.DataFrame(rows).set_index("id", drop=False)
    SUBS = sorted(P["sub"].dropna().unique().tolist())
    si = {s: i for i, s in enumerate(SUBS)}
    GO = {"L": 0, "M": 1, "P": 2, "H": 3, "O": 4}
    shards = collections.defaultdict(dict)
    idx, pj = [], {}
    porano = collections.defaultdict(collections.Counter)
    for d in sens:
        sid = d["id"]
        ks = [int(p.get("codigoMateria") or p.get("id")) for p in (PR.get(sid) or [])]
        pp = P.loc[[k for k in dict.fromkeys(ks)]] if ks else P.iloc[0:0]
        pr = {k: (autoria.get(k) or [0])[0] == sid for k in pp.index}
        pp = pp.assign(go=pp.g.map(GO)).sort_values(["go", "et", "dt"], ascending=[True, True, False])
        items = [[int(r.id), r.lab, r.dt, r.g, si.get(r.sub, -1) if isinstance(r.sub, str) else -1, int(r.et), int(pr.get(r.id, False)),
                  cortar(r.em, 170)] for r in pp.itertuples()]
        shards[sid % 64][sid] = items
        vc = pp.g.value_counts()
        aprov = int(sum(1 for r in pp.itertuples() if r.g == "L" and r.et in (1, 2) and pr.get(r.id)))
        idx.append([sid] + [int(vc.get(k, 0)) for k in "LMPHO"] + [int((pp.et == 1).sum()), int(sum(pr.values())), aprov])
        pj[sid] = int(pp.sig.isin(["PL", "PLP", "PEC", "PDL"]).sum())
        for r in pp.itertuples():
            porano[sid][r.dt[:4]] += 1
    os.makedirs(os.path.join(saida, "s"), exist_ok=True)
    for k in range(64):
        json.dump(shards.get(k, {}), open(os.path.join(saida, "s", f"{k}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    RES = {"subs": SUBS, "idx": idx}
    PP = [[d["id"], 0, 0, pj.get(d["id"], 0), 0] for d in sens]
    # busca e repetidas
    B = P[P.g.isin(list("LMPH"))].copy()
    B["ufs"] = B.em.map(ufs)
    os.makedirs(os.path.join(saida, "busca"), exist_ok=True)
    for y in ANOS:
        d = B[B.dt.str[:4] == str(y)].sort_values("dt", ascending=False)
        items = [[int(r.id), r.lab, r.dt, r.g, si.get(r.sub, -1) if isinstance(r.sub, str) else -1, int(r.et), cortar(r.em, 220), "",
                  ",".join(r.ufs), [x for x in autoria.get(r.id, []) if x]] for r in d.itertuples()]
        json.dump(items, open(os.path.join(saida, "busca", f"b{y}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    nomes = {d["id"]: [d["nome"], d["partido"], d["uf"]] for d in sens}
    json.dump(nomes, open(os.path.join(saida, "busca", "nomes.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    B["k"] = B.em.map(chave)
    rep = []
    for (t, k), d in B.groupby(["sig", "k"]):
        if len(d) < 2 or len(k) < 25 or (GEN.match(k) and " para " not in k and " a fim de " not in k and " com o objetivo " not in k) or VAGO.search(k):
            continue
        d = d.sort_values("dt")
        rep.append([t, cortar(d.em.iloc[0], 260), len(d), ",".join(sorted(set(sum(list(d.ufs), [])))), d.g.iloc[0],
                    [[int(r.id), r.lab, r.dt, ([x for x in autoria.get(r.id, []) if x] or [0])[0], len(autoria.get(r.id, [])), r.sit.title()]
                     for r in d.itertuples()]])
    rep.sort(key=lambda g: -g[2])
    json.dump(rep, open(os.path.join(saida, "busca", "repetidas.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    stats = {"propostas": int(len(P)), "assinaturas": int(sum(len(v or []) for v in PR.values())),
             "grupos": {g: int(n) for g, n in P.g.value_counts().items()}, "repetidas": len(rep)}
    return RES, PP, porano, stats


# ------------------------------------------------------------------ custos e emendas
def meses_em_exercicio(periodos, hoje):
    meses = set()
    for ini, fim in periodos or []:
        if not ini:
            continue
        a = max(ini[:10], INICIO)
        b = min((fim or hoje.strftime("%Y-%m-%d"))[:10], hoje.strftime("%Y-%m-%d"))
        if a > b:
            continue
        y, m = int(a[:4]), int(a[5:7])
        while (y, m) <= (int(b[:4]), int(b[5:7])):
            meses.add((y, m))
            m += 1
            if m == 13:
                y, m = y + 1, 1
    return meses


def nome_cgu(s):
    s = norm(s).upper()
    s = re.sub(r"[^A-Z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


FUNCOES = {"Encargos especiais": "Transferência especial (o município ou estado decide onde usar)"}


def setor_de(tipo, funcao):
    if "Especiais" in (tipo or ""):
        return "Transferência especial (o município ou estado decide onde usar)"
    f = (funcao or "Outros").strip()
    return FUNCOES.get(f, f[:1].upper() + f[1:])


def calcular_custos_emendas(sens, MAND, CE, RU, EMd, OFFv, DEPS, RES, hoje, saida):
    # CEAPS por senador e ano (lançamentos), conferido com o total agrupado de recursos-utilizados
    ceaps = collections.defaultdict(float)
    mensal = collections.defaultdict(lambda: [0.0] * 12)
    for a in ANOS:
        for r in (CE.get(a) or []):
            if (r.get("tipoDespesa") or "").strip():  # lançamentos sem tipo não entram no total oficial agrupado
                ceaps[(int(r.get("codSenador") or 0), a)] += float(r.get("valorReembolsado") or 0)
                try:
                    m = int(r.get("mes") or 0)
                except (TypeError, ValueError):
                    m = 0
                if 1 <= m <= 12:
                    mensal[(int(r.get("codSenador") or 0), a)][m - 1] += float(r.get("valorReembolsado") or 0)
    # gasto mês a mês de cada senador (cota parlamentar): {id: [[ano, [cota x12], [zeros x12]], ...]}
    gsh = collections.defaultdict(dict)
    for d in sens:
        lin = [[a, [round(x, 2) for x in mensal[(d["id"], a)]], [0] * 12] for a in ANOS if sum(mensal.get((d["id"], a), [0]))]
        if lin:
            gsh[d["id"] % 64][d["id"]] = lin
    os.makedirs(os.path.join(saida, "g"), exist_ok=True)
    for k in range(64):
        json.dump(gsh.get(k, {}), open(os.path.join(saida, "g", f"{k}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    CU, difs = [], []
    for d in sens:
        i = d["id"]
        meses = meses_em_exercicio(MAND.get(i), hoje)
        linha = [i, 0.0]
        for a in ANOS:
            ms = [m for (y, m) in meses if y == a]
            sal = sum(subsidio(a, m) for m in ms)
            ru = RU.get((i, a)) or {}
            dd = (ru.get("data") or [{}])[0] if isinstance(ru, dict) else {}
            outros = float(((dd.get("gastosNaoInclusos") or {}).get("totalValor")) or 0)
            cota_ru = float(((dd.get("cotas") or {}).get("totalValor")) or 0)
            cota_lanc = round(ceaps.get((i, a), 0.0), 2)
            # no ano corrente as duas bases do Senado se atualizam em dias diferentes; a conferência vale para anos fechados
            if a < hoje.year and abs(cota_lanc - cota_ru) > max(100, 0.01 * max(cota_ru, cota_lanc)):
                difs.append((i, a, cota_lanc, cota_ru))
            cota = round(cota_ru, 2)
            linha += [cota, round(outros, 2), round(sal, 2), len(ms)]
            linha[1] += cota + outros + sal
        linha[1] = round(linha[1], 2)
        CU.append(linha)
    # pessoal e benefícios (ano mais recente com dados)
    PES = []
    for d in sens:
        info = [d["id"], 0, 0, "", ""]
        for a in reversed(ANOS):
            ru = RU.get((d["id"], a)) or {}
            dd = (ru.get("data") or [None])[0] if isinstance(ru, dict) else None
            if not dd:
                continue
            for p in dd.get("pessoal") or []:
                if "Gabinete" in (p.get("local") or ""):
                    info[1] = p.get("quantidade") or 0
                else:
                    info[2] += p.get("quantidade") or 0
            for b in dd.get("beneficios") or []:
                if "Moradia" in (b.get("beneficio") or ""):
                    info[3] = b.get("utilizacao") or ""
                if "Funcional" in (b.get("beneficio") or ""):
                    info[4] = b.get("utilizacao") or ""
            break
        PES.append(info)
    # emendas (Portal da Transparência, CGU)
    SETS = []
    si = {}
    EMD, shards = [], collections.defaultdict(dict)
    sem_match = []
    ambiguos = []
    if EMd is not None:
        e = EMd.copy()
        e["ano"] = pd.to_numeric(e["Ano da Emenda"], errors="coerce")
        e = e[e.ano.isin(ANOS_EMENDAS) & e["Tipo de Emenda"].str.startswith("Emenda Individual", na=False)]
        for c in ["Valor Empenhado", "Valor Liquidado", "Valor Pago", "Valor Restos A Pagar Pagos"]:
            e[c] = e[c].fillna("0").str.replace(".", "", regex=False).str.replace(",", ".", regex=False).astype(float)
        e["nm"] = e["Nome do Autor da Emenda"].map(nome_cgu)
        e = e.rename(columns={"Nome Função": "funcao", "Nome Subfunção": "subfuncao", "Nome Ação": "acao",
                              "Localidade de aplicação do recurso": "local", "Valor Liquidado": "liq", "Valor Empenhado": "emp"})
        e["pg"] = e["Valor Pago"] + e["Valor Restos A Pagar Pagos"]
        e["setor"] = [setor_de(t_, f) for t_, f in zip(e["Tipo de Emenda"], e["funcao"])]
        for d in sens:
            alvo = {nome_cgu(d["nome"]), nome_cgu(d["completo"])}
            x = e[e.nm.isin(alvo)]
            codigos = set(zip(x.ano, x["Código do Autor da Emenda"]))
            if len({c for _, c in codigos}) > len(set(x.ano)):
                ambiguos.append(d["nome"])
            if x.empty:
                sem_match.append(d["nome"])
            itens = []
            por = collections.defaultdict(float)
            for r in x.itertuples(index=False):
                s = r.setor
                if s not in si:
                    si[s] = len(SETS); SETS.append(s)
                por[si[s]] += r.pg
                itens.append([int(r.ano), f"{r.funcao} / {r.subfuncao}", f"{str(r.acao).capitalize()} - {r.local}",
                              round(r.liq, 2), round(r.emp, 2), round(r.pg, 2), si[s]])
            itens.sort(key=lambda z: (-z[0], -z[5]))
            shards[d["id"] % 64][d["id"]] = itens
            EMD.append([d["id"], round(float(x.liq.sum()), 2), round(float(x.emp.sum()), 2),
                        round(float(x.pg.sum()), 2), len(itens), por, 1 if x.empty else 0])
        for r in EMD:
            r[5] = [round(r[5].get(k, 0.0), 2) for k in range(len(SETS))]
        SETT = []
        mine = collections.defaultdict(float)
        for r in EMD:
            for k, v in enumerate(r[5]):
                mine[k] += v
        SETT = [round(mine[k], 2) for k in range(len(SETS))]
    else:
        SETT = []
    os.makedirs(os.path.join(saida, "e"), exist_ok=True)
    for k in range(64):
        json.dump(shards.get(k, {}), open(os.path.join(saida, "e", f"{k}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    # nota
    dep_by = {r[0]: r for r in DEPS}
    res_by = {r[0]: r for r in RES["idx"]}
    cu_by = {r[0]: r for r in CU}
    vals = {"pres": {}, "vot": {}, "leis": {}, "custo": {}}
    for d in sens:
        i = d["id"]
        p = sum(OFFv[(i, a)][0] for a in ANOS); t = sum(sum(OFFv[(i, a)]) for a in ANOS)
        if t >= 20:
            vals["pres"][i] = 100 * p / t
        r = dep_by.get(i)
        if r and r[5] >= 20:
            vals["vot"][i] = 100 * r[4] / r[5]
        vals["leis"][i] = res_by.get(i, [0] * 9)[8]
        c = cu_by[i]
        meses = sum(c[2 + 4 * k + 3] for k in range(len(ANOS)))
        gasto = sum(c[2 + 4 * k] + c[2 + 4 * k + 1] for k in range(len(ANOS)))
        if meses >= 3:
            vals["custo"][i] = gasto / meses

    def escala(v, todos, maior_melhor=True):
        menos = sum(1 for x in todos if x < v); iguais = sum(1 for x in todos if x == v)
        q = (menos + 0.5 * (iguais - 1)) / max(1, len(todos) - 1)
        return round(10 * (q if maior_melhor else 1 - q), 1)

    todos = {k: sorted(v.values()) for k, v in vals.items()}
    NOTA = []
    for d in sens:
        i = d["id"]; sc = []; raw = []
        for k, mm in [("pres", True), ("vot", True), ("leis", True), ("custo", False)]:
            v = vals[k].get(i)
            raw.append(None if v is None else round(v, 2))
            sc.append(None if v is None else escala(v, todos[k], mm))
        ok = [x for x in sc if x is not None]
        NOTA.append([i, round(sum(ok) / len(ok), 1) if ok else None] + sc + raw)
    stats = {"custo_total": round(sum(r[1] for r in CU), 2), "emendas_pago": round(sum(r[3] for r in EMD), 2),
             "com_custo": sum(1 for r in CU if r[1] > 0), "difs_ceaps": difs, "sem_emendas": sem_match, "ambiguos": ambiguos,
             "com_emendas": sum(1 for r in EMD if r[4] > 0)}
    return CU, EMD, NOTA, SETS, SETT, PES, stats


# ------------------------------------------------------------------ conferência
def conferir(atual, anterior, sens, sv, sp, sc, faltas, CAM, EMd):
    prob = []
    if not 78 <= len(sens) <= 81:
        prob.append(f"a lista oficial tem {len(sens)} senadores em exercício (esperado 81)")
    for k, v in faltas.items():
        if v:
            prob.append(f"não foi possível ler {k}: {v[:5]}")
    for y in ANOS[:-1]:
        if sv["por_ano"].get(str(y), 0) == 0:
            prob.append(f"nenhuma votação nominal encontrada em {y}")
    if sv["votacoes"] < 150:
        prob.append(f"só {sv['votacoes']} votações nominais de 2023 até hoje (esperado bem mais)")
    if sv["sem_votos"]:
        prob.append(f"{len(sv['sem_votos'])} votações abertas sem a lista de votos completa")
    sem_placar = [v for v in sv["placar"] if not v[1]]
    if len(sem_placar) > max(2, len(sv["placar"]) // 4):
        prob.append(f"{len(sem_placar)} votações principais não bateram com o placar oficial do painel")
    if sp["propostas"] < 3000:
        prob.append(f"só {sp['propostas']} propostas encontradas (esperado alguns milhares)")
    if sc["com_custo"] < 75:
        prob.append(f"só {sc['com_custo']} senadores com custo do mandato")
    if len(sc["difs_ceaps"]) > 8:
        prob.append(f"cota parlamentar de {len(sc['difs_ceaps'])} senador-ano não bate entre as duas fontes do Senado")
    if EMd is None:
        prob.append("não foi possível baixar as emendas do Portal da Transparência")
    elif sc["com_emendas"] < 60:
        prob.append(f"só {sc['com_emendas']} senadores com emendas encontradas (esperado perto de 81)")
    if CAM is None:
        prob.append("não foi possível ler o gasto do Senado na página de execução orçamentária")
    if anterior:
        a0 = anterior.get("contagens", {})
        for k, v in atual["contagens"].items():
            v0 = a0.get(k)
            if isinstance(v0, (int, float)) and isinstance(v, (int, float)) and v0 > 0 and v < v0 * 0.99:
                if k == "gasto_senado" and atual["data"][3:] != anterior.get("data", "")[3:] and atual["data"][3:5] == "01":
                    continue
                prob.append(f"{k} caiu de {v0:,} para {v:,} desde a última versão publicada")
    return prob


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=os.path.join(RAIZ, "saida"))
    ap.add_argument("--dados", default=os.path.join(RAIZ, "dados"))
    ap.add_argument("--salvar-rep", dest="salvar_rep")
    ap.add_argument("--hoje")
    a = ap.parse_args()
    tz = dt.timezone(dt.timedelta(hours=-3))
    hoje = dt.datetime.fromisoformat(a.hoje).replace(tzinfo=tz) if a.hoje else dt.datetime.now(tz)
    saida = a.saida
    os.makedirs(saida, exist_ok=True)
    rel = {"inicio": hoje.isoformat(), "site": SITE_URL}
    try:
        log("1/7 senadores em exercício")
        sens = coletar_senadores()
        ids = [d["id"] for d in sens]
        log(f"  {len(sens)} senadores")
        log("2/7 mandatos (períodos em exercício)")
        MAND = coletar_mandatos(ids)
        log("3/7 votações nominais e orientações")
        V, O = coletar_votacoes(hoje)
        log("4/7 proposições de cada senador")
        PR = coletar_processos(ids, hoje)
        log("5/7 cota parlamentar e recursos usados")
        CE, RU = coletar_custos(ids)
        log("6/7 emendas (Portal da Transparência) e gasto do Senado")
        EMd = coletar_emendas(a.dados)
        CAM = coletar_gasto_senado(hoje)
        if CAM:
            CAM["em"] = hoje.isoformat()
        log(f"  gasto do Senado: {CAM}")
        faltas = {"mandatos": [i for i, v in MAND.items() if v is None],
                  "votações": [y for y, v in V.items() if v is None],
                  "orientações": [y for y, v in O.items() if v is None],
                  "proposições": [i for i, v in PR.items() if v is None],
                  "cota parlamentar (CEAPS)": [y for y, v in CE.items() if v is None],
                  "recursos usados": [k for k, v in RU.items() if v is None]}
        R, MX, DEPS, VY, VOT, COD, OFFv, sv = calcular_votacoes(V, O, sens)
        log(f"  {sv['votacoes']} votações nominais ({sv['abertas']} abertas), {len(VOT)} principais divididas, Governo em {sv['gov']}")
        os.makedirs(os.path.join(saida, "u"), exist_ok=True)
        for k in range(64):
            json.dump({i: v for i, v in sv["ult"].items() if i % 64 == k}, open(os.path.join(saida, "u", f"{k}.json"), "w"),
                      ensure_ascii=False, separators=(",", ":"))
        RES, PP, porano, sp = calcular_propostas(PR, sens, saida)
        log(f"  {sp['propostas']:,} propostas")
        CU, EMD, NOTA, SETS, SETT, PES, sc = calcular_custos_emendas(sens, MAND, CE, RU, EMd, OFFv, DEPS, RES, hoje, saida)
        NRC = carregar_posicoes(); NR = None
        if sv["alin"]:
            NRn = nota_nasruas(NOTA, sv["alin"], NRC)
            com = [v for v in NRn.values() if v[0] is not None]
            log(f"  nota Nas Ruas: {len(NRC['votacoes'])} votações marcadas, {len(com)} senadores com nota nova"
                + (f", média {sum(v[0] for v in com) / len(com):.1f}" if com else "") + ("" if NRC["ativa"] else " (ainda não ativa no site)"))
            if NRC["ativa"] and com:
                NR = {"pesos": NRC["pesos"], "n": len([k for k, v in NRC["votacoes"].items() if v.get("posicao") in ("S", "N")]),
                      "minimo": NRC["minimo_votacoes"], "a": {i: v[1:] for i, v in NRn.items()}}
                for r in NOTA:
                    r[1] = NRn[r[0]][0]
        log(f"  custo dos mandatos: R$ {sc['custo_total']:,.2f}; emendas pagas: R$ {sc['emendas_pago']:,.2f}; "
            f"{sc['com_emendas']} com emendas; sem emendas: {sc['sem_emendas']}; ambíguos: {sc['ambiguos']}; difs CEAPS: {sc['difs_ceaps'][:5]}")
        atual = {"data": hoje.strftime("%d/%m/%Y"),
                 "contagens": {"votacoes": sv["votacoes"], "votos": sv["votos"], "propostas": sp["propostas"],
                               **{f"votacoes_{y}": sv["por_ano"][str(y)] for y in ANOS},
                               "custo_mandatos": sc["custo_total"], "emendas_pagas": sc["emendas_pago"],
                               "gasto_senado": CAM["pago"] if CAM else 0}}
        anterior = None
        try:
            r = requests.get(SITE_URL + "manifest.json", timeout=30)
            if r.status_code == 200:
                anterior = r.json()
                log(f"  comparando com a versão publicada em {anterior.get('data')}")
        except Exception:
            pass
        log("7/7 conferência")
        prob = conferir(atual, anterior, sens, sv, sp, sc, faltas, CAM, EMd)
        rel["problemas"] = prob
        rel["contagens"] = atual["contagens"]
        if prob:
            for p in prob:
                log("  PROBLEMA: " + p)
            raise SystemExit(2)
        log("  tudo conferido")
        OFFl = [[d["id"]] + sum([[porano[d["id"]].get(str(y), 0), VY["v"][k][1 + j]] + OFFv[(d["id"], y)]
                                 for j, y in enumerate(ANOS)], []) for k, d in enumerate(sens)]
        t = open(os.path.join(RAIZ, "modelo", "index.html"), encoding="utf-8").read()
        J = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        rep = {"__DEPS__": J(DEPS), "__VY__": J(VY), "__RES__": J(RES), "__OFF__": J(OFFl), "__PP__": J(PP), "__COD__": J(COD),
               "__VOT__": J(VOT), "__RANK__": J(R), "__MATRIX__": J(MX), "__DATA__": atual["data"],
               "__NPROP__": f"{sp['propostas']:,}".replace(",", "."), "__NVOT__": f"{sv['votacoes']:,}".replace(",", "."),
               "__ULTIMA__": "/".join(R["meta"]["ultimaVotacao"][:10].split("-")[::-1]), "__N21__": str(len(VOT)),
               "__CUS__": J(CU), "__EMD__": J(EMD), "__NOTA__": J(NOTA), "__SET__": J(SETS), "__SETT__": J(SETT), "__CAM__": J(CAM),
               "__PES__": J(PES), "__NR__": J(NR)}
        if a.salvar_rep:
            json.dump(rep, open(a.salvar_rep, "w"), ensure_ascii=False)
        for k, v in rep.items():
            if k not in t:
                raise RuntimeError(f"marcador {k} não está no modelo")
            t = t.replace(k, v)
        open(os.path.join(saida, "index.html"), "w", encoding="utf-8").write(t)
        # lista resumida para a entrada unificada do Radar Nas Ruas (www.nasruas.com.br/radar/)
        ntm = {r[0]: r for r in NOTA}
        lista = {"casa": "senado", "data": atual["data"], "custo_total": sc.get("custo_total"),
                 "emendas_pago": sc.get("emendas_pago"), "CAM": CAM,
                 "itens": [[r[0], r[1], r[2], r[3]] + ([ntm[r[0]][1], ntm[r[0]][6], ntm[r[0]][9]] if r[0] in ntm else [None, None, None])
                           for r in DEPS]}
        json.dump(lista, open(os.path.join(saida, "lista.json"), "w"), ensure_ascii=False, separators=(",", ":"))
        json.dump(atual, open(os.path.join(saida, "manifest.json"), "w"), ensure_ascii=False, indent=1)
        open(os.path.join(saida, ".nojekyll"), "w").close()
        rel["ok"] = True
        log("PRONTO: site montado em " + saida)
    finally:
        rel["log"] = LOG
        json.dump(rel, open(os.path.join(RAIZ, "relatorio.json"), "w"), ensure_ascii=False, indent=1)
        resumo = os.environ.get("GITHUB_STEP_SUMMARY")
        if resumo:
            with open(resumo, "a") as f:
                f.write("## Robô do Radar NasRuas Senado\n\n")
                f.write("**Resultado:** " + ("publicado ✅" if rel.get("ok") else "NÃO publicado: o site continua com a última versão boa ❌") + "\n\n")
                for p in rel.get("problemas", []):
                    f.write(f"- {p}\n")
                f.write("\n```\n" + "\n".join(LOG[-60:]) + "\n```\n")


if __name__ == "__main__":
    main()
