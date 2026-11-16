# Regra única de classificação das propostas dos deputados federais (2023-2026)
# Fonte: proposicoes-ANO.csv e proposicoesAutores-ANO.csv (Dados Abertos da Câmara)
import re, unicodedata
def norm(s):
    s=str(s or '').lower()
    s=''.join(c for c in unicodedata.normalize('NFD',s) if unicodedata.category(c)!='Mn')
    return re.sub(r'\s+',' ',s).strip()

LEI={'PL','PLP','PEC','PDL','PRC','PLV'}
# 1) Homenagens: por tipo oficial
H_TIPO={'Requerimento de Voto de regozijo ou louvor':'Votos de parabéns e louvor',
        'Requerimento de Voto de pesar':'Votos de pesar',
        'Requerimento de Sessão Solene':'Sessões solenes',
        'Requerimento de Inserção nos Anais':'Votos de parabéns e louvor'}
# 1b) Homenagens dentro de projetos: datas, nomes, títulos, prêmios, patrimônio
H_LEI=re.compile(r'^\W*(institui|cria|declara|confere|concede|estabelece|constitui|inscreve|denomina|da (o )?nome|altera a denominacao|reconhece|considera|atribui|eleva|inclui|o presente projeto de lei visa instituir).{0,140}?(\bdia (nacional|mundial|internacional|estadual|municipal)\b|\bsemana nacional|\bmes nacional|\bano nacional|data comemorativa|calendario oficial|calendario de datas|calendario nacional de (datas|eventos|festas)|capital nacional|\bcapital (da|do|dos|das) |patron[oa]\b|livro dos herois|titulo de|medalha|premio|comenda|diploma\b|ordem do merito|patrimonio cultural|patrimonio historico|manifestacao da cultura nacional|cultura nacional)|^\W*denomina|^\W*da (o )?nome|^\W*inscreve|capital nacional|livro dos herois|^esta lei declara.{0,120}(capital|patrimonio)|para (instituir|incluir|criar).{0,60}(dia nacional|semana nacional|mes nacional|data comemorativa|no calendario)')
H_REQ=re.compile(r'(voto|mocao|mocoes)\s+de\s+(louvor|aplauso|congratula|pesar|regozijo|solidariedade|reconhecimento|agradecimento|parabens|homenagem)|homenage|sessao solene|congratula')
# 2) Pedidos de melhoria para o país
M_SIGLA={'INC','SOR','SLD','SPP','EMO','EML'}
M_TIPO={'Requerimento para envio de Indicação pela Comissão'}
# 3) Perguntas e cobranças
P_SIGLA={'RIC','SIT','PFC','RCP'}
P_TIPO={'Requerimento para envio de Requerimento de Informação pela Comissão','Requerimento para envio de ofício solicitando informações',
 'Requerimento para envio de Solicitação de Informação ao TCU pela Comissão','Requerimento de Convocação de Ministro de Estado na Comissão (art. 50, CF)',
 'Requerimento de Convocação de Ministro de Estado no Plenário (art. 50, CF)','Requerimento de Convocação de Autoridade','Requerimento de Convocação em CPI (art. 58, §3º, CF)',
 'Requerimento de Requisição de Informação e Documentação em CPI (art. 58, §3º, CF)','Requerimento de Quebra de Sigilo em CPI (art. 58, §3º, CF)',
 'Requerimento de Realização de Diligências em CPI (art. 58, §3º, CF)','Requerimento de Constituição de Comissão Externa'}
# 5) Outros: subgrupos
EMENDA={'EMC','EMP','EMR','ESB','SBT','SBR','SSP','EMA','ERD','SBE','DTQ','SBT-A','SBE-A','EMC-A'}
RELAT={'PRL','PRLP','PPP','PEP','PRLE','PSS','PES','VTS','CVO','RDF','REL','RRL','PRV','PAR','PPR','PRR','RAT','RPL','RPLE','RPLOA','RLF','RLP','RRC','COI','CAE','CFIS','MAN'}
EVENTO={'Requerimento de Audiência Pública','Requerimento para realização ou participação em Seminário, Visita Técnica ou outro Evento',
 'Requerimento de Transformação de Sessão Plenária em Comissão Geral','Requerimento de Audiência solicitada por Comissão ou Deputado'}
INTERNO_SIGLA={'DOC','OF','PROC','ATA','PRO','APJ','ERR','ADD','CON','PIN','INA'}

def grupo(r):
    s=r.siglaTipo; t=r.descricaoTipo; e=norm(r.ementa)
    if t in H_TIPO: return 'H',H_TIPO[t]
    if t=='Requerimento de Moção':
        if 'repudio' in e or 'protesto' in e: return 'O','Protestos e repúdios'
        return 'H','Moções de louvor, aplauso e solidariedade'
    if s in LEI:
        if H_LEI.search(e):
            if re.search(r'\bdia (nacional|mundial|internacional|estadual|municipal)\b|semana nacional|mes nacional|ano nacional|data comemorativa|calendario oficial|calendario de datas',e) and not re.search(r'^\W*(denomina|da (o )?nome)',e): return 'H','Datas comemorativas'
            if re.search(r'^\W*(denomina|da (o )?nome)|altera a denominacao',e): return 'H','Nomes de rodovias, obras e lugares'
            return 'H','Títulos, prêmios, heróis e patrimônio cultural'
        return 'L',None
    if s in M_SIGLA or t in M_TIPO: return 'M','Indicações ao governo' if s=='INC' or t in M_TIPO else 'Sugestões de verba no Orçamento'
    if s in P_SIGLA or t in P_TIPO:
        if s=='RIC' or 'Informação' in t or 'ofício' in t or s=='SIT': return 'P','Pedidos de informação a ministérios e órgãos'
        if 'Convocação' in t: return 'P','Convocação de ministros e autoridades'
        if s=='RCP' or 'CPI' in t: return 'P','CPIs (investigações)'
        return 'P','Fiscalização e comissões externas'
    if s=='REQ' and t in ('Requerimento (Outros)','Requerimento') and H_REQ.search(e): return 'H','Votos de parabéns e louvor'
    if s in EMENDA: return 'O','Mudanças em projetos (emendas)'
    if s in RELAT: return 'O','Relatórios e pareceres como relator'
    if t in EVENTO: return 'O','Audiências públicas, debates e eventos'
    if s in INTERNO_SIGLA or 'Frente Parlamentar' in t or 'Grupo Parlamentar' in t or 'Assinatura' in t: return 'O','Organização interna da Câmara'
    if s in ('RPD','RPDR','REC') or s=='REQ': return 'O','Pedidos sobre a pauta e o andamento das votações'
    return 'O','Demais'

def etapa(sit):
    s=str(sit or '')
    if 'Norma Jurídica' in s: return 1  # virou lei
    if s in ('Aguardando Apreciação pelo Senado Federal','Aguardando Sanção','Aguardando Autógrafos na Mesa','Aguardando Envio ao Senado Federal','Aguardando Apreciação do Veto'): return 2
    if s=='Pronta para Pauta': return 3
    if s in ('Arquivada','Retirado pelo(a) Autor(a)','Devolvida ao(à) Autor(a)','Prejudicialidade','Transformado em nova proposição'): return 5
    return 4
