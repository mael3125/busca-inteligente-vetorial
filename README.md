# 🛒 Busca Inteligente Vetorial

> **Não é uma busca por palavra-chave. É uma busca por intenção.**

Digite "algo pra lavar as mãos" e ela retorna sabonete, não arroz. Digite "internet" e ela não retorna nada, porque entende que nenhum produto é internet. Isso é busca semântica.

[Demo ao vivo](https://mael3125.github.io/busca-inteligente-vetorial) • [API no Render](https://sua-api.onrender.com/docs)

### 🎯 O Problema
Buscas tradicionais com `LIKE '%termo%'` falham:
- "algo pra cozinhar" → 0 resultados
- "internet" → retorna arroz (porque tem "net" no nome? bug clássico)
- "quero fazer dieta" → não encontra nada saudável

### ✅ A Solução
Usei **pgvector + embeddings multilíngues** para transformar cada produto em um vetor de 384 dimensões. A busca vira uma comparação de significado, não de letras.

### 🔍 Exemplos Reais

| Você digita | Ela entende e retorna | Match |
|-------------|----------------------|-------|
| `algo para cozinhar` | Óleo, Sal, Arroz | 85% |
| `algo para lavar as mãos` | Sabonete Líquido | 91% |
| `limpeza` | Detergente, Esponja, Sabonete | 78% |
| `quero fazer dieta` | Arroz Integral, Feijão | 73% |
| `internet` | *Nada* (correto!) | - |

### 🧠 Como funciona

```
Texto do produto ("Detergente para louça") 
  → intfloat/multilingual-e5-small 
  → vetor [0.12, -0.45, 0.88... 384 dims]
  → salvo no Supabase com pgvector

Sua busca ("algo pra lavar louça")
  → mesmo modelo
  → mesmo espaço vetorial
  → SELECT * ORDER BY embedding <=> busca LIMIT 5
```

- **Threshold 0.02**: Filtra lixos. Se score < 0.4, não retorna nada (por isso "internet" não retorna arroz)
- **Modelo**: `intfloat/multilingual-e5-small` - leve, rápido, português nativo

### 🚀 Tecnologias

- **Banco**: Supabase (Postgres + pgvector)
- **Backend**: FastAPI + psycopg2
- **IA**: sentence-transformers + torch
- **Frontend**: HTML/JS puro (sem framework)
- **Deploy**: Render (API) + GitHub Pages (site)

### 💻 Rodar local

```bash
# 1. Clone
git clone https://github.com/mael3125/busca-inteligente-vetorial

# 2. Crie .env com sua DATABASE_URL do Supabase
# DATABASE_URL=postgresql://postgres:...

# 3. Instale
pip install -r requirements.txt

# 4. Crie a tabela (rode 1x no SQL Editor do Supabase)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE produtos (
  id SERIAL PRIMARY KEY,
  nome TEXT,
  descricao TEXT,
  preco FLOAT,
  embedding VECTOR(384)
);

# 5. Popule os embeddings (seu script de ingestão)
# 6. Rode a API
uvicorn api:app --reload

# 7. Abra index.html
```

### 📦 API

```
GET /buscar?q=algo para cozinhar

{
  "resultados": [
    {
      "nome": "Óleo de Soja",
      "descricao": "Ideal para cozinhar",
      "preco": 8.5,
      "score": 0.85
    }
  ]
}
```

Docs automáticos: `/docs`

### 📈 Próximos passos

- [ ] Reranking com cross-encoder
- [ ] Busca híbrida (vetorial + keyword)
- [ ] Filtros por preço/categoria
- [ ] Avaliação com MRR e Precision@K

---
Feito por [@mael3125](https://github.com/mael3125) - Estudo de banco vetorial na prática
