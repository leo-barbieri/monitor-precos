"""Monitor de preços PS5 / GTA 6 — sem IA, roda no GitHub Actions.

Fontes: busca da KaBuM (dados estruturados) + prévia pública de canais do Telegram.
Saídas: data/historico.csv (só grava quando algo muda), alerta no Telegram quando o
PS5 fica <= preço-alvo (parcelado, crédito ou Pix), resumo diário com o menor preço de cada produto.
Opcional: espelha o histórico numa tabela do Supabase (SUPABASE_URL / SUPABASE_KEY).
"""
import csv
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(RAIZ, "config.json"), encoding="utf-8"))
HIST = os.path.join(RAIZ, "data", "historico.csv")
ESTADO = os.path.join(RAIZ, "data", "estado.json")
SP = timezone(timedelta(hours=-3))  # São Paulo (sem horário de verão desde 2019)
AGORA = datetime.now(timezone.utc)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
COLUNAS = ["checado_em", "produto", "fonte", "loja", "vendedor", "titulo",
           "preco_parcelado", "parcelas", "preco_pix", "cupom", "verificado", "link"]

ACESSORIO = re.compile(
    r"\b(controle|dualsense|leitor|drive|unidade|tampa|capa|suporte|base|bolsa|mochila|"
    r"cooler|hub|fonte|headset|fone|carregador|skin|adesivo|cabo|case|volante|jogo|game|"
    r"usado|seminovo|vitrine|japon[eê]s|110v|importado|recondicionado|portal|pulse|ssd|nvme|"
    r"c[aâ]mbio|memória|cart[aã]o|gift|assinatura|plus)s?\b", re.I)
CONSOLE = re.compile(r"\b(console|playstation\s*5|ps5)\b", re.I)
CONSOLE_FORTE = re.compile(r"\b(slim|digital|825\s*gb|1\s*tb|2\s*tb|pro)\b", re.I)
GTA6 = re.compile(r"\b(gta\s*(6|vi)|grand\s+theft\s+auto\s*(6|vi))\b", re.I)
GTA6_RUIM = re.compile(r"\b(suporte|controle|camiseta|poster|pôster|chaveiro|ps4|xbox|pc|steam|guia|vinil|"
                       r"album|álbum|trilha|smart\s*tv|tv|combo|livro|funko|mapa)\b", re.I)


def log(*a):
    print(*a, file=sys.stderr)


def baixar(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def classificar(titulo):
    """Retorna 'ps5', 'gta6' ou None."""
    if GTA6.search(titulo) and not GTA6_RUIM.search(titulo):
        return "gta6"
    if re.search(r"\b(usado|seminovo|vitrine|japon[eê]s|110v|importado|recondicionado|ps4|ps3)\b", titulo, re.I):
        return None
    # "Console PlayStation 5 ..., Controle DualSense" é o console (o controle vem junto)
    sem_loja = re.sub(r"^\W*(\[[^\]]*\]|#\w+)?\W*", "", titulo)  # tira "🔥 [Amazon]" / "#Aliexpress -"
    if re.match(r"console\b", sem_loja, re.I) and re.search(
            r"playstation\s*5|ps5", titulo, re.I) and not re.search(r"portal|controle\s+para", titulo, re.I):
        return "ps5"
    if CONSOLE.search(titulo) and CONSOLE_FORTE.search(titulo) and not ACESSORIO.search(titulo):
        return "ps5"
    return None


def br(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if v else "-"


# ---------------------------------------------------------------- KaBuM
def coletar_kabum():
    ofertas, vistos = [], set()
    for produto, termos in CFG["kabum_buscas"].items():
        for termo in termos:
            url = f"https://www.kabum.com.br/busca/{termo}"
            try:
                h = baixar(url)
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
                dados = json.loads(m.group(1))["props"]["pageProps"]["data"]
                if isinstance(dados, str):  # páginas de categoria vêm com o JSON serializado
                    dados = json.loads(dados)
                itens = dados["catalogServer"]["data"]
            except Exception as e:  # loja fora do ar / layout mudou
                log(f"KaBuM falhou em {termo}: {e}")
                continue
            for it in itens:
                if it["code"] in vistos or not it.get("available"):
                    continue
                tipo = classificar(it["name"])
                if tipo != produto:
                    continue
                vistos.add(it["code"])
                n_parc = int(re.match(r"(\d+)", it.get("maxInstallment") or "1").group(1))
                ofertas.append({
                    "produto": tipo, "fonte": "KaBuM", "loja": "KaBuM",
                    "vendedor": it.get("sellerName", ""), "titulo": it["name"][:120],
                    "preco_parcelado": float(it["price"]), "parcelas": it.get("maxInstallment", ""),
                    "n_parcelas": n_parc, "preco_pix": float(it.get("priceWithDiscount") or 0),
                    "cupom": "", "verificado": "sim",
                    "link": f"https://www.kabum.com.br/produto/{it['code']}/{it.get('friendlyName', '')}",
                })
    return ofertas


# ---------------------------------------------------------------- lojas VTEX (Americanas)
def coletar_vtex():
    ofertas, vistos = [], set()
    for loja, cfg in CFG.get("lojas_vtex", {}).items():
        for produto, termos in cfg["buscas"].items():
            for termo in termos:
                url = (f"https://{cfg['host']}/api/catalog_system/pub/products/search"
                       f"?ft={urllib.parse.quote(termo)}&_from=0&_to=49")
                try:
                    itens = json.loads(baixar(url))
                except Exception as e:
                    log(f"{loja} falhou em {termo}: {e}")
                    continue
                for p in itens:
                    if p["productId"] in vistos or classificar(p["productName"]) != produto:
                        continue
                    for vend in p["items"][0]["sellers"]:
                        o = vend["commertialOffer"]
                        if not o.get("AvailableQuantity") or not o.get("Price"):
                            continue
                        sem_juros = [i for i in o.get("Installments", []) if i.get("InterestRate", 0) == 0
                                     and i["PaymentSystemName"] in ("Visa", "Mastercard")]
                        melhor = max(sem_juros, key=lambda i: i["NumberOfInstallments"], default=None)
                        pix = next((i["Value"] for i in o.get("Installments", []) if i["PaymentSystemName"] == "Pix"), 0)
                        vistos.add(p["productId"])
                        ofertas.append({
                            "produto": produto, "fonte": loja, "loja": loja, "vendedor": vend["sellerName"].title(),
                            "titulo": p["productName"][:120], "preco_parcelado": float(o["Price"]),
                            "parcelas": f"{melhor['NumberOfInstallments']}x de {br(melhor['Value'])}" if melhor else "",
                            "n_parcelas": melhor["NumberOfInstallments"] if melhor else 1,
                            "preco_pix": float(pix), "cupom": "", "verificado": "sim", "link": p["link"],
                        })
                        break
    return ofertas


# ---------------------------------------------------------------- Telegram
DINHEIRO = r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"


def extrair_precos(texto):
    """Lê preço Pix e parcelado de um post. Ignora preço antigo ('De R$ X por'),
    valor da parcela ('10x de R$ 99'), cupom ('R$ 100 OFF') e mínimos ('acima de R$ 199')."""
    t = re.sub(r"\bde\s+" + DINHEIRO + r"\s+(por|para)\b", "por", texto, flags=re.I)
    n_parc = 0
    m = re.search(r"(\d{1,2})\s*x\s*(?:de\s+)?" + DINHEIRO, t, re.I)
    if m:
        n_parc = int(m.group(1))
    t = re.sub(r"\d{1,2}\s*x\s*(?:de\s+)?" + DINHEIRO, " ", t, flags=re.I)
    t = re.sub(DINHEIRO + r"\s*(off|de desconto|de volta|cashback)", " ", t, flags=re.I)
    t = re.sub(r"(acima|a partir|limite|limitado|m[ií]nimo)\s+(de|em|a)?\s*" + DINHEIRO, " ", t, flags=re.I)
    pix = parcelado = 0.0
    for linha in t.split("\n"):
        for v in re.findall(DINHEIRO + r"|\bpor\s+(\d{1,3}(?:\.\d{3})*,\d{2})", linha, re.I):
            valor = _valor(v[0] or v[1])
            if not valor:
                continue
            if re.search(r"pix|[àa] vista|boleto", linha, re.I):
                pix = pix or valor
            else:
                parc = re.search(r"(\d{1,2})\s*x", linha, re.I)
                if parc:
                    n_parc = n_parc or int(parc.group(1))
                parcelado = parcelado or valor
    return pix, parcelado, n_parc


LOJAS_LINK = {"meli.la": "Mercado Livre", "mercadolivre": "Mercado Livre", "amazon": "Amazon",
              "amzn": "Amazon", "shopee": "Shopee", "magazineluiza": "Magalu", "magalu": "Magalu",
              "kabum": "KaBuM", "casasbahia": "Casas Bahia", "aliexpress": "AliExpress",
              "carrefour": "Carrefour", "fastshop": "Fast Shop", "tidd.ly": "Magalu (afiliado)"}


def _valor(s):
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def coletar_telegram():
    ofertas, limite = [], AGORA - timedelta(hours=24)
    for canal, termos in CFG["canais_telegram"].items():
        for q in termos:
            try:
                h = baixar(f"https://t.me/s/{canal}?q={urllib.parse.quote(q)}")
            except Exception as e:
                log(f"Telegram {canal} falhou: {e}")
                continue
            for bloco in h.split('class="tgme_widget_message_wrap')[1:]:
                post = re.search(r'data-post="([^"]+)"', bloco)
                quando = re.search(r'<time datetime="([^"]+)"', bloco)
                corpo = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', bloco, re.S)
                if not (post and quando and corpo):
                    continue
                dt = datetime.fromisoformat(quando.group(1))
                if dt < limite:
                    continue
                bruto = corpo.group(1)
                texto = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", bruto)))
                if "<s>" in bruto or "<del>" in bruto or re.search(
                        r"esgot|acabou|encerrad|expirou|sem estoque", texto, re.I):
                    continue
                linhas = [l.strip() for l in texto.split("\n") if l.strip()]
                # título = 1ª linha que bate com o produto e NÃO é a chamada em CAIXA ALTA
                tipo, titulo = None, ""
                for l in linhas:
                    letras = [c for c in l if c.isalpha()]
                    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.8:
                        continue
                    tipo = classificar(l)
                    if tipo:
                        titulo = l
                        break
                if not tipo:
                    continue
                pix, parcelado, n_parc = extrair_precos(texto)
                faixa = (CFG["ps5_piso_post"], 8000) if tipo == "ps5" else (100, 1000)  # fora disso é combo/erro
                if not (faixa[0] <= max(pix, parcelado) <= faixa[1]):
                    continue
                links = [l for l in re.findall(r'href="(https?://[^"]+)"', bruto) if "t.me/" not in l]
                if not links:
                    continue
                link = html.unescape(links[0])
                loja_txt = re.search(r"\[([^\]]+)\]|#(\w+)", texto)
                loja = (next((n for k, n in LOJAS_LINK.items() if k in link), None)
                        or (loja_txt and (loja_txt.group(1) or loja_txt.group(2)))
                        or next((n for k, n in LOJAS_LINK.items() if k in texto.lower()), "?"))
                cupom = re.search(r"cupom[:\s]*([A-Z0-9]{4,})", texto, re.I)
                preco = parcelado or pix
                ofertas.append({
                    "produto": tipo, "fonte": f"Telegram: {canal}", "loja": loja,
                    "vendedor": "", "titulo": titulo[:120],
                    "preco_parcelado": parcelado,
                    "parcelas": f"{n_parc}x" if n_parc else "",
                    "n_parcelas": n_parc,
                    "preco_pix": pix,
                    "cupom": cupom.group(1) if cupom else "", "verificado": "não",
                    "link": link, "post": f"https://t.me/{post.group(1)}",
                    "hora_post": dt.astimezone(SP).strftime("%d/%m %H:%M"), "dt_post": dt,
                })
    # mesmo post aparece em mais de uma busca
    unicos = {o["post"]: o for o in ofertas}
    return list(unicos.values())


# ---------------------------------------------------------------- estado / histórico
def carregar_estado():
    try:
        return json.load(open(ESTADO, encoding="utf-8"))
    except FileNotFoundError:
        return {"ultimo": {}, "alertados": [], "resumo_enviado": ""}


def assinatura(o):
    return f"{o['preco_parcelado']}|{o['parcelas']}|{o['preco_pix']}|{o['cupom']}"


def registrar(ofertas, estado):
    """Grava no CSV só o que é novo ou mudou de preço. Retorna as linhas gravadas."""
    novas = []
    agora_sp = AGORA.astimezone(SP).strftime("%Y-%m-%d %H:%M")
    for o in ofertas:
        chave = o.get("post") or o["link"]
        if estado["ultimo"].get(chave) == assinatura(o):
            continue
        estado["ultimo"][chave] = assinatura(o)
        linha = {c: o.get(c, "") for c in COLUNAS}
        linha["checado_em"] = agora_sp
        if o.get("post"):
            linha["fonte"] = f"{o['fonte']} (post {o['hora_post']})"
        novas.append(linha)
    # ofertas da loja que sumiram da busca
    links_atuais = {o["link"] for o in ofertas if o["verificado"] == "sim"}
    for chave, sig in list(estado["ultimo"].items()):
        if not chave.startswith("https://t.me/") and chave not in links_atuais and sig != "saiu":
            estado["ultimo"][chave] = "saiu"
            novas.append({**{c: "" for c in COLUNAS}, "checado_em": agora_sp, "fonte": "loja",
                          "verificado": "saiu da loja", "link": chave})
    if novas:
        existe = os.path.exists(HIST)
        with open(HIST, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUNAS)
            if not existe:
                w.writeheader()
            w.writerows(novas)
        enviar_supabase(novas)
    return novas


def enviar_supabase(linhas):
    url, chave = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and chave):
        return
    corpo = []
    for l in linhas:
        r = dict(l)
        for c in ("preco_parcelado", "preco_pix"):
            r[c] = float(r[c]) if r[c] not in ("", None, 0, 0.0) else None
        r["checado_em"] = datetime.strptime(l["checado_em"], "%Y-%m-%d %H:%M").replace(tzinfo=SP).isoformat()
        corpo.append(r)
    req = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/precos", data=json.dumps(corpo).encode(), method="POST",
        headers={"apikey": chave, "Authorization": f"Bearer {chave}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        log(f"Supabase falhou (histórico local segue salvo): {e}")


# ---------------------------------------------------------------- 3 categorias de preço
CATS = (("parcelado", "Crédito parcelado"), ("credito", "Crédito"), ("pix", "Pix"))
CURTO = {"parcelado": "Parcelado", "credito": "Crédito", "pix": "Pix"}


def mesmo_vendedor(o):
    return not o["vendedor"] or re.sub(r"\W", "", o["vendedor"]).lower() == re.sub(r"\W", "", o["loja"]).lower()
NOME_PROD = {"ps5": "PS5", "gta6": "GTA 6"}


def valores(o):
    """pix = à vista no Pix; credito = no cartão (qualquer nº de parcelas);
    parcelado = no cartão em >= parcelas_minimas sem juros."""
    cred = o["preco_parcelado"] or 0.0
    return {"pix": o["preco_pix"] or 0.0, "credito": cred,
            "parcelado": cred if o["n_parcelas"] >= CFG["parcelas_minimas"] else 0.0}


def confiavel(o):
    if o["verificado"] != "sim":
        return True  # canal de promo: sinalizado como não verificado
    return any(v.lower() in o["vendedor"].lower() for v in CFG["vendedores_confiaveis"])


def melhores(ofertas, prod):
    """Menor valor de cada categoria entre as ofertas atuais: {cat: (valor, oferta)}."""
    res = {}
    for o in ofertas:
        if o["produto"] != prod or not confiavel(o):
            continue
        for cat, v in valores(o).items():
            if v and (cat not in res or v < res[cat][0]):
                res[cat] = (v, o)
    return res


# ---------------------------------------------------------------- Telegram bot
def enviar_telegram(texto):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log("[sem TELEGRAM_BOT_TOKEN/CHAT_ID] mensagem:\n" + texto)
        return
    # TELEGRAM_CHAT_ID aceita vários destinos separados por vírgula (ex.: "123456789,@meucanal")
    for destino in [c.strip() for c in chat.split(",") if c.strip()]:
        dados = urllib.parse.urlencode({"chat_id": destino, "text": texto, "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dados, timeout=20)
        except Exception as e:  # um destino com problema não impede os outros
            log(f"Telegram falhou para um destino: {e}")


def selo(o):
    return "✅ verificado na loja" if o["verificado"] == "sim" else f"⚠️ não verificado · {o['fonte']} {o['hora_post']}"


def linha_msg(o):
    v = valores(o)
    vend = "" if mesmo_vendedor(o) else f" ({html.escape(o['vendedor'])})"
    partes = []
    if v["parcelado"]:
        partes.append(f"💳 {br(v['parcelado'])} em {html.escape(o['parcelas'])}")
    elif v["credito"]:
        partes.append(f"💳 {br(v['credito'])}{' ' + html.escape(o['parcelas']) if o['parcelas'] else ''}")
    if v["pix"]:
        partes.append(f"Pix {br(v['pix'])}")
    extra = f"\n🎟 cupom {html.escape(o['cupom'])}" if o["cupom"] else ""
    return (f"<b>{html.escape(o['loja'])}{vend}</b> — {html.escape(o['titulo'][:70])}\n"
            f"{' | '.join(partes)}{extra}\n{selo(o)}\n{html.escape(o['link'])}")


def alertas(ofertas, estado):
    """(a) PS5 no preço-alvo (parcelado, crédito ou Pix); (b) novo menor preço em cada categoria."""
    alvo, msgs = CFG["ps5_preco_alvo"], []
    lim_post = AGORA - timedelta(hours=CFG["horas_max_post_telegram"])
    recentes = [o for o in ofertas if not (o.get("dt_post") and o["dt_post"] < lim_post)]

    # (a) preço-alvo — vale para parcelado (foco), crédito e Pix
    for o in recentes:
        if o["produto"] != "ps5" or not confiavel(o):
            continue
        v = valores(o)
        bateu = [nome for cat, nome in CATS if 0 < v[cat] <= alvo]
        if not bateu:
            continue
        chave = f"alvo|{o.get('post') or o['link']}|{assinatura(o)}"
        if chave in estado["alertados"]:
            continue
        estado["alertados"].append(chave)
        msgs.append(f"🚨 <b>PS5 ≤ {br(alvo)}</b> — {', '.join(bateu)}\n" + linha_msg(o))

    # (b) recordes por categoria (só PS5; GTA 6 fica no painel e no resumo)
    rec = estado.setdefault("recordes", {})
    for prod in ("ps5",):
        atual = melhores(recentes, prod)
        rp = rec.setdefault(prod, {})
        por_oferta = {}
        for cat, nome in CATS:
            if cat not in atual:
                continue
            valor, o = atual[cat]
            ant = rp.get(cat)
            if ant and valor > ant["valor"] - 1:  # precisa cair pelo menos R$ 1
                continue
            rp[cat] = {"valor": valor, "link": o["link"], "loja": o["loja"],
                       "quando": AGORA.astimezone(SP).strftime("%d/%m %H:%M")}
            if ant:  # na 1ª execução só registra, sem alertar
                chave = o.get("post") or o["link"]
                por_oferta.setdefault(chave, (o, []))[1].append(
                    f"• {nome}: <b>{br(valor)}</b> (antes {br(ant['valor'])}, −{br(ant['valor'] - valor)})")
        for o, linhas in por_oferta.values():
            msgs.append(f"📉 <b>Novo menor preço {NOME_PROD[prod]}</b>\n" + "\n".join(linhas) + "\n" + linha_msg(o))
    estado["alertados"] = estado["alertados"][-500:]
    if msgs:
        cab = f"🔔 {AGORA.astimezone(SP):%d/%m %H:%M}\n\n"
        enviar_telegram(cab + "\n\n".join(msgs) + "\n\nConfira o preço no carrinho antes de pagar.")
    return len(msgs)


def atualizar_diario(ofertas, estado):
    """Menor valor de cada categoria por dia (base da tabela do painel)."""
    dia = AGORA.astimezone(SP).strftime("%Y-%m-%d")
    d = estado.setdefault("diario", {}).setdefault(dia, {})
    for prod in NOME_PROD:
        dp = d.setdefault(prod, {})
        for cat, (v, _) in melhores(ofertas, prod).items():
            if not dp.get(cat) or v < dp[cat]:
                dp[cat] = v
    for velho in sorted(estado["diario"])[:-60]:
        del estado["diario"][velho]


def resumo_diario(ofertas, estado, forcar=False):
    hoje = AGORA.astimezone(SP)
    if not forcar and (hoje.hour < CFG["hora_resumo_diario"] or estado.get("resumo_enviado") == hoje.strftime("%Y-%m-%d")):
        return False
    estado["resumo_enviado"] = hoje.strftime("%Y-%m-%d")
    blocos = [f"📊 <b>Resumo {hoje:%d/%m %H:%M}</b>"]
    for prod, emoji in (("ps5", "🎮"), ("gta6", "🕹️")):
        m = melhores(ofertas, prod)
        blocos.append(f"{emoji} <b>{NOME_PROD[prod]} — menores preços agora</b>")
        if not m:
            blocos.append("Nenhuma oferta confiável encontrada.")
            continue
        for cat, nome in CATS:
            if cat in m:
                v, o = m[cat]
                blocos.append(f"<b>{nome}: {br(v)}</b>\n" + linha_msg(o))
    url = os.getenv("PAGINA_URL")
    if url:
        blocos.append(f"📱 Todos os preços: {url}")
    enviar_telegram("\n\n".join(blocos))
    return True


# ---------------------------------------------------------------- painel (página de preços atuais)
def gerar_painel(ofertas, estado):
    agora = AGORA.astimezone(SP)
    alvo = CFG["ps5_preco_alvo"]
    e = html.escape

    def card(o, prod):
        v = valores(o)
        no_alvo = prod == "ps5" and any(0 < v[c] <= alvo for c, _ in CATS)
        cel = []
        for cat, nome in CATS:
            sub = e(o["parcelas"]) if cat == "parcelado" and v[cat] else ("à vista" if cat == "pix" and v[cat] else "")
            cel.append(f'<div class="v"><span>{CURTO[cat]}</span><b>{br(v[cat]) if v[cat] else "—"}</b><small>{sub}</small></div>')
        obs = []
        if o["cupom"]:
            obs.append(f'🎟 Cupom <code>{e(o["cupom"])}</code>')
        if not mesmo_vendedor(o):
            obs.append(f"Vendido por {e(o['vendedor'])}")
        obs.append('<span class="ok">✅ lido na loja</span>' if o["verificado"] == "sim"
                   else f'<span class="wa">⚠️ post de {e(o["fonte"].replace("Telegram: ", ""))} às {o["hora_post"]}</span>')
        return (f'<article class="c{" alvo" if no_alvo else ""}"><div class="h"><b>{e(o["loja"])}</b>'
                f'{"<em>NO ALVO</em>" if no_alvo else ""}</div><div class="t">{e(o["titulo"])}</div>'
                f'<div class="g">{"".join(cel)}</div><div class="o">{" · ".join(obs)}</div>'
                f'<a class="b" href="{e(o["link"])}" target="_blank" rel="noopener">Abrir oferta →</a></article>')

    def secao(prod, titulo):
        m = melhores(ofertas, prod)
        rec = estado.get("recordes", {}).get(prod, {})
        tiles = "".join(
            f'<div class="k"><span>{CURTO[cat]}</span><b>{br(m[cat][0]) if cat in m else "—"}</b>'
            f'<small>{e(m[cat][1]["loja"]) if cat in m else ""}'
            f'{" · recorde " + br(rec[cat]["valor"]) if cat in rec else ""}</small></div>' for cat, nome in CATS)
        cands = [o for o in ofertas if o["produto"] == prod and confiavel(o)]
        cands.sort(key=lambda o: min([x for x in valores(o).values() if x] or [9e9]))
        cards = "".join(card(o, prod) for o in cands[:15]) or '<p class="mu">Nenhuma oferta confiável agora.</p>'
        dias = sorted(estado.get("diario", {}), reverse=True)[:14]
        linhas = "".join(
            f"<tr><td>{d[8:10]}/{d[5:7]}</td>" + "".join(
                f"<td>{br(estado['diario'][d].get(prod, {}).get(c, 0))}</td>" for c, _ in CATS) + "</tr>" for d in dias)
        return (f'<h2>{titulo}</h2><div class="kpis">{tiles}</div>{cards}'
                f'<details><summary>Menor preço por dia</summary><table><tr><th>Dia</th>'
                + "".join(f"<th>{n}</th>" for _, n in CATS) + f"</tr>{linhas}</table></details>")

    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>Preços PS5 e GTA 6</title><style>
:root{{--bg:#f6f6f4;--card:#fff;--tx:#1d1d1b;--mu:#6b6b66;--bd:#e3e3de;--ok:#1a7f37;--wa:#9a6700;--al:#e3f6e8;--alb:#1a7f37;--ac:#2456c9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--card:#1f1f1d;--tx:#ececea;--mu:#a3a39d;--bd:#33332f;--ok:#4ac26b;--wa:#d4a72c;--al:#16301d;--alb:#4ac26b;--ac:#7aa2ff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.4 system-ui,-apple-system,sans-serif}}
main{{max-width:680px;margin:auto;padding:16px}}h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:18px;margin:28px 0 10px}}
.mu{{color:var(--mu);font-size:13px}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:6px}}
.k{{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:8px}}
.k span,.v span{{display:block;font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.03em}}
.k b{{display:block;font-size:16px;font-variant-numeric:tabular-nums}}.k small,.v small{{display:block;font-size:11px;color:var(--mu)}}
.c{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px;margin:10px 0}}
.c.alvo{{background:var(--al);border-color:var(--alb)}}.h{{display:flex;justify-content:space-between;align-items:center}}
.h em{{font-style:normal;font-size:11px;font-weight:700;color:var(--alb);border:1px solid var(--alb);border-radius:6px;padding:1px 6px}}
.t{{font-size:13px;color:var(--mu);margin:2px 0 8px}}.g{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}
.v b{{display:block;font-size:15px;font-variant-numeric:tabular-nums}}.o{{font-size:13px;color:var(--mu);margin-top:8px}}
code{{font-size:12px;background:var(--bg);border:1px solid var(--bd);border-radius:4px;padding:0 4px}}
.ok{{color:var(--ok)}}.wa{{color:var(--wa)}}
.b{{display:block;text-align:center;margin-top:10px;padding:9px;border-radius:8px;background:var(--ac);color:#fff;text-decoration:none;font-weight:600}}
details{{margin-top:6px}}summary{{cursor:pointer;color:var(--mu);font-size:13px;padding:6px 0}}
table{{border-collapse:collapse;width:100%;background:var(--card);font-size:13px;font-variant-numeric:tabular-nums}}
td,th{{border:1px solid var(--bd);padding:6px;text-align:right}}td:first-child,th:first-child{{text-align:left}}
</style></head><body><main>
<h1>🎮 Preços PS5 e GTA 6</h1>
<div class="mu">Checado em {agora:%d/%m/%Y %H:%M} (Brasília) · alvo PS5 {br(alvo)} no crédito parcelado, no crédito ou no Pix</div>
{secao("ps5", "PS5 — console")}{secao("gta6", "GTA 6 — PS5")}
<p class="mu">Crédito = preço no cartão · Crédito parcelado = no cartão em {CFG["parcelas_minimas"]}x ou mais sem juros · ✅ lido na página da loja · ⚠️ preço informado por canal do Telegram. Confira no carrinho antes de pagar.</p>
</main></body></html>"""
    os.makedirs(os.path.join(RAIZ, "site"), exist_ok=True)
    open(os.path.join(RAIZ, "site", "index.html"), "w", encoding="utf-8").write(pagina)


def main():
    forcar_resumo = "--resumo" in sys.argv
    estado = carregar_estado()
    ofertas = coletar_kabum() + coletar_vtex() + coletar_telegram()
    novas = registrar(ofertas, estado)
    n_alertas = alertas(ofertas, estado)
    atualizar_diario(ofertas, estado)
    resumo = resumo_diario(ofertas, estado, forcar_resumo)
    gerar_painel(ofertas, estado)
    estado["ultima_execucao"] = AGORA.astimezone(SP).isoformat(timespec="minutes")
    json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"ofertas={len(ofertas)} gravadas={len(novas)} alertas={n_alertas} resumo={resumo}")


if __name__ == "__main__":
    main()
