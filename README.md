# Monitor de preços — PS5 e GTA 6

Roda sozinho no GitHub Actions, **sem IA e sem tokens**. A cada execução ele:

1. Lê a busca da **KaBuM** (preço parcelado, Pix, parcelas e vendedor, incluindo produtos Magalu vendidos lá).
2. Lê a prévia pública de **7 canais de promoção do Telegram**. Ignora posts riscados ou com cupom esgotado, e só alerta posts com até 6 h.
3. Grava em `data/historico.csv` **apenas o que mudou** (preço novo, oferta nova, saiu da loja).
4. Manda **alerta no Telegram** quando um PS5 fica **≤ R$ 3.500** (no mínimo 3x quando é da loja).
5. Manda um **resumo diário às 9h** com o PS5 mais barato (top 3) e o GTA 6 (top 2).

Frequência: a cada **10 min de 26 a 30/11** e a cada **2 h** no resto do ano. O GitHub pode atrasar alguns minutos em horário de pico.

Os parâmetros (preço-alvo, canais, buscas e vendedores confiáveis) ficam em `config.json`.

## Configuração (uma vez)

### 1. Bot do Telegram (≈ 3 min)
1. No Telegram, fale com **@BotFather** → `/newbot` → escolha um nome. Ele devolve o **token**.
2. Abra o seu bot e mande qualquer mensagem (ex.: "oi").
3. No navegador, abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o número em `"chat":{"id": ...}`. Esse é o **chat_id**.

### 2. Secrets no GitHub
No repositório: **Settings → Secrets and variables → Actions → New repository secret**

| Nome | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token do BotFather |
| `TELEGRAM_CHAT_ID` | seu chat_id |
| `SUPABASE_URL` / `SUPABASE_KEY` | *opcional*: só se usar o Supabase (veja abaixo) |

### 3. Testar
**Actions → Monitor de precos → Run workflow**, marque *"Enviar o resumo agora"*. O resumo deve chegar no Telegram em ~1 min.

## Supabase (opcional)
O histórico já fica no CSV do repositório. Se quiser um banco para consultar via SQL ou pelo MCP do Supabase no Claude:
1. Crie um projeto em supabase.com e rode `supabase/schema.sql` no SQL Editor.
2. Cadastre `SUPABASE_URL` e `SUPABASE_KEY` (chave **service_role**) nos secrets.

## Limites conhecidos
- **Magalu e Casas Bahia** bloqueiam robôs (erro 403). As ofertas delas chegam pelos canais do Telegram.
- Ofertas do **Telegram** vêm marcadas "⚠️ não verificado": o preço é o que o post informou.
- Repositório **privado** tem 2.000 min/mês grátis no Actions. Esta agenda usa cerca de 1.100 min em novembro e cerca de 400 min nos outros meses. Repositório público não tem limite.
