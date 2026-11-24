# Monitor de preços — PS5 e GTA 6

Roda sozinho no GitHub Actions, **sem IA e sem tokens**. A cada execução ele:

1. Lê direto nas lojas **KaBuM** e **Americanas** (preço parcelado, Pix, parcelas e vendedor; as duas também listam produtos vendidos pela Magalu), e o comparador **Zoom**, que traz a melhor oferta de cada produto entre lojas como Amazon, Magalu e Webcontinental.
2. Lê a prévia pública de **7 canais de promoção do Telegram**. Ignora posts riscados ou com cupom esgotado, e só alerta posts com até 6 h.
3. Grava em `data/historico.csv` **apenas o que mudou** (preço novo, oferta nova, saiu da loja).
4. Separa cada oferta em **3 preços**: **Parcelado** (cartão em 3x ou mais sem juros), **Crédito** (cartão, qualquer número de parcelas) e **Pix**.
5. Manda **alerta no Telegram** quando:
   - um PS5 fica **≤ R$ 3.500** no crédito parcelado (foco), no crédito ou no Pix;
   - aparece um **novo menor preço do PS5** em qualquer uma das 3 categorias (queda mínima de R$ 1; uma mensagem por oferta).
6. Manda um **resumo diário às 9h** com o menor preço de cada categoria (PS5 e GTA 6).
7. Atualiza a **página de preços atuais** (GitHub Pages), com os 3 preços, cupom, vendedor e botão para a oferta de cada loja, e o menor preço por dia.

Frequência: a cada **10 min de 24 a 30/11** e a cada **2 h** no resto do ano. O GitHub pode atrasar alguns minutos em horário de pico.

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

### 3. Página de preços (GitHub Pages)
**Settings → Pages → Source: GitHub Actions**. O endereço fica `https://<usuario>.github.io/<repositorio>/`. No plano gratuito, só funciona com repositório público.

### 4. Testar
**Actions → Monitor de precos → Run workflow**, marque *"Enviar o resumo agora"*. O resumo deve chegar no Telegram em ~1 min.

## Supabase (opcional)
O histórico já fica no CSV do repositório. Se quiser um banco para consultar via SQL ou pelo MCP do Supabase no Claude:
1. Crie um projeto em supabase.com e rode `supabase/schema.sql` no SQL Editor.
2. Cadastre `SUPABASE_URL` e `SUPABASE_KEY` (chave **service_role**) nos secrets.

## Limites conhecidos
- **Leitura direta bloqueada** (anti-robô): Mercado Livre, Amazon, Shopee, AliExpress, Magalu, Casas Bahia, Carrefour, Pichau, Terabyte e Google Shopping (captcha). Amazon e Magalu entram pelo Zoom quando têm o menor preço de um produto; as demais lojas chegam pelos canais do Telegram.
- O **Zoom** mostra só a melhor oferta de cada produto, não a lista completa de lojas. O botão leva à página do Zoom, e de lá "Ir à loja" abre a oferta.
- Ofertas do **Telegram** vêm marcadas "⚠️ não verificado": o preço é o que o post informou.
- Repositório **privado** tem 2.000 min/mês grátis no Actions. Esta agenda usa cerca de 1.100 min em novembro e cerca de 400 min nos outros meses. Repositório público não tem limite.
