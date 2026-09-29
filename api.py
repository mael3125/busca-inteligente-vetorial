import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from sentence_transformers import SentenceTransformer

# Pega a URL do ambiente (Render) ou usa a local se não tiver
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgresql://postgres:vpS720D8a1jrd6T8@db.wssltptloqjghdjnudoa.supabase.co:5432/postgres"
)

print("Carregando modelo E5...")
model = SentenceTransformer('intfloat/multilingual-e5-small')
print("Modelo carregado!")

app = FastAPI(title="Busca Inteligente Vetorial")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_conn():
    return psycopg2.connect(DATABASE_URL)

@app.get("/")
def home():
    return {"status": "ok", "mensagem": "API Busca Inteligente no ar!"}

@app.get("/buscar")
def buscar(q: str):
    vetor = model.encode(f"query: {q}").tolist()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
      SELECT id, nome, descricao, preco, 1 - (embedding <=> %s::vector) as score
      FROM produtos ORDER BY embedding <=> %s::vector LIMIT 10
    """, (vetor, vetor))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return {"busca": q, "resultados": []}

    top_score = float(rows[0][4])

    # REGRA 1: Se nem o melhor resultado for bom, não mostra nada (caso "internet")
    if top_score < 0.83:
        return {"busca": q, "resultados": []}

    # REGRA 2: Só mostra o que estiver a até 2% do primeiro lugar
    # Com 100 produtos, 2% já separa "lavar mãos" de "detergente"
    resultados = []
    for id_p, nome, desc, preco, score in rows:
        s = float(score)
        if s >= top_score - 0.02:
            resultados.append({
                "nome": nome, "descricao": desc,
                "preco": float(preco) if preco else None,
                "score": round(s, 2)
            })

    return {"busca": q, "resultados": resultados}
