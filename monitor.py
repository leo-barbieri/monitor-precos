"""Monitor de preços PS5 / GTA 6 — sem IA, roda no GitHub Actions.

Fontes: busca da KaBuM (dados estruturados) + prévia pública de canais do Telegram.
Saídas: data/historico.csv (só grava quando algo muda), alerta no Telegram quando o
PS5 fica <= preço-alvo, resumo diário com o menor preço de cada produto.
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
                itens = json.loads(m.group(1))["props"]["pageProps"]["data"]["catalogServer"]["data"]
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
                faixa = (1500, 8000) if tipo == "ps5" else (100, 1000)  # fora disso é combo/erro
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
        if "kabum.com.br" in chave and chave not in links_atuais and sig != "saiu":
            estado["ultimo"][chave] = "saiu"
            novas.append({**{c: "" for c in COLUNAS}, "checado_em": agora_sp, "fonte": "KaBuM",
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


# ---------------------------------------------------------------- Telegram bot
def enviar_telegram(texto):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log("[sem TELEGRAM_BOT_TOKEN/CHAT_ID] mensagem:\n" + texto)
        return
    dados = urllib.parse.urlencode({"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dados, timeout=20)


def linha_msg(o):
    selo = "✅ verificado na loja" if o["verificado"] == "sim" else f"⚠️ {o['fonte']} {o['hora_post']}"
    vend = f" ({o['vendedor']})" if o["vendedor"] and o["vendedor"] != o["loja"] else ""
    partes = [f"<b>{o['loja']}{vend}</b>"]
    if o["preco_parcelado"]:
        partes.append(f"{br(o['preco_parcelado'])} {o['parcelas']}".strip())
    if o["preco_pix"]:
        partes.append(f"Pix {br(o['preco_pix'])}")
    if o["cupom"]:
        partes.append(f"cupom {o['cupom']}")
    return " | ".join(partes) + f"\n{selo}\n{o['link']}"


def confiavel(o):
    if o["verificado"] != "sim":
        return True  # canal de promo: sinalizado como não verificado
    return any(v.lower() in o["vendedor"].lower() for v in CFG["vendedores_confiaveis"])


def alertas_ps5(ofertas, estado):
    alvo, msgs = CFG["ps5_preco_alvo"], []
    lim_post = AGORA - timedelta(hours=CFG["horas_max_post_telegram"])
    for o in ofertas:
        if o["produto"] != "ps5" or not confiavel(o):
            continue
        if o.get("dt_post") and o["dt_post"] < lim_post:
            continue
        preco = o["preco_parcelado"] or o["preco_pix"]
        if not preco or preco > alvo:
            continue
        if o["preco_parcelado"] and o["verificado"] == "sim" and o["n_parcelas"] < CFG["parcelas_minimas"]:
            continue
        chave = f"{o.get('post') or o['link']}|{assinatura(o)}"
        if chave in estado["alertados"]:
            continue
        estado["alertados"].append(chave)
        msgs.append(linha_msg(o))
    estado["alertados"] = estado["alertados"][-500:]
    if msgs:
        cab = f"🚨 <b>PS5 ≤ {br(alvo)}</b> — {AGORA.astimezone(SP):%d/%m %H:%M}\n\n"
        enviar_telegram(cab + "\n\n".join(msgs) + "\n\nConfira o preço no carrinho antes de pagar.")
    return len(msgs)


def resumo_diario(ofertas, estado, forcar=False):
    hoje = AGORA.astimezone(SP)
    if not forcar and (hoje.hour < CFG["hora_resumo_diario"] or estado.get("resumo_enviado") == hoje.strftime("%Y-%m-%d")):
        return False
    estado["resumo_enviado"] = hoje.strftime("%Y-%m-%d")
    blocos = [f"📊 <b>Resumo {hoje:%d/%m %H:%M}</b>"]
    for prod, nome, n in (("ps5", "🎮 PS5 (mais barato)", 3), ("gta6", "🕹️ GTA 6", 2)):
        cands = [o for o in ofertas if o["produto"] == prod and confiavel(o)]
        cands.sort(key=lambda o: o["preco_parcelado"] or o["preco_pix"] or 9e9)
        blocos.append(f"\n<b>{nome}</b>")
        blocos += [linha_msg(o) for o in cands[:n]] or ["Nenhuma oferta confiável encontrada."]
    enviar_telegram("\n\n".join(blocos))
    return True


def main():
    forcar_resumo = "--resumo" in sys.argv
    estado = carregar_estado()
    ofertas = coletar_kabum() + coletar_telegram()
    novas = registrar(ofertas, estado)
    n_alertas = alertas_ps5(ofertas, estado)
    resumo = resumo_diario(ofertas, estado, forcar_resumo)
    estado["ultima_execucao"] = AGORA.astimezone(SP).isoformat(timespec="minutes")
    json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"ofertas={len(ofertas)} gravadas={len(novas)} alertas={n_alertas} resumo={resumo}")


if __name__ == "__main__":
    main()
