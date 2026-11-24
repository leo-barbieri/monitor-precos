-- Opcional: espelho do histórico no Supabase (para consultar pelo MCP / SQL).
-- Rode no SQL Editor do projeto e cadastre SUPABASE_URL e SUPABASE_KEY (service_role) nos secrets do GitHub.
create table if not exists public.precos (
  id               bigint generated always as identity primary key,
  checado_em       timestamptz not null,
  produto          text not null,          -- 'ps5' | 'gta6'
  fonte            text,                   -- 'KaBuM' | 'Telegram: canal (post dd/mm hh:mm)'
  loja             text,
  vendedor         text,
  titulo           text,
  preco_parcelado  numeric(10,2),
  parcelas         text,
  preco_pix        numeric(10,2),
  cupom            text,
  verificado       text,                   -- 'sim' | 'não' | 'saiu da loja'
  link             text
);
alter table public.precos enable row level security;  -- só a service_role escreve/lê

-- Exemplo de consulta: menor preço verificado do PS5 por dia
-- select date(checado_em at time zone 'America/Sao_Paulo') dia, min(preco_parcelado)
-- from precos where produto='ps5' and verificado='sim' group by 1 order by 1 desc;
