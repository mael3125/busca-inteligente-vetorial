import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import requests

# Configurações
DATABASE_URL = os.environ.get("DATABASE_URL")
# Token grátis do HuggingFace - você pode criar um em huggingface.co/settings/tokens
HF_TOKEN = os.environ.get("HF_TOKEN", "")  # Deixe vazio que ainda funciona limitado

print("API leve iniciada - usando HuggingFace Inference API")

app = FastAPI(title="Busca Inteligente Vetorial - Light")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def gerar_embedding(texto: str):
    """Gera embedding via HuggingFace Inference API (sem precisar torch)"""
    # API pública do Hugging Face
    api_url = "https://api-inference.huggingface.co/pipeline/feature-extraction/intfloat/multilingual-e5-small"
    headers = {}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"
    
    # Formato E5 precisa de "query: " na frente
    payload = {"inputs": f"query: {texto}", "options": {"wait_for_model": True}}
    
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            embedding = response.json()
            # Se vier aninhado [[...]], pega o primeiro
            if isinstance(embedding, list) and len(embedding) > 0 and isinstance(embedding[0], list) and isinstance(embedding[0][0], list):
                embedding = embedding[0][0]
            elif isinstance(embedding, list) and len(embedding) > 0 and isinstance(embedding[0], list):
                embedding = embedding[0]
            return embedding
        else:
            print(f"Erro HF API {response.status_code}: {response.text}")
            # Fallback: retorna None e tenta busca por texto se falhar
            return None
    except Exception as e:
        print(f"Erro ao chamar HF: {e}")
        return None

@app.get("/")
def home():
    return {"status": "ok", "mensagem": "API Busca Inteligente Light no ar! Sem torch."}

@app.get("/buscar")
def buscar(q: str):
    vetor = gerar_embedding(q)
    
    # Se falhar o embedding externo, faz busca simples por similaridade de texto como fallback
    conn = get_conn()
    cur = conn.cursor()
    
    if vetor is None:
        # Fallback: busca por ILIKE ainda melhor que nada
        cur.execute("""
          SELECT id, nome, descricao, preco, 0.8 as score
          FROM produtos 
          WHERE nome ILIKE %s OR descricao ILIKE %s
          LIMIT 10
        """, (f"%{q}%", f"%{q}%"))
    else:
        cur.execute("""
          SELECT id, nome, descricao, preco, 1 - (embedding <=> %s::vector) as score
          FROM produtos ORDER BY embedding <=> %s::vector LIMIT 10
        """, (vetor, vetor))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return {"busca": q, "resultados": []}

    # Se foi embedding, aplica regras de threshold
    if vetor is not None:
        top_score = float(rows[0][4])
        if top_score < 0.83:
            return {"busca": q, "resultados": []}

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
    else:
        # Fallback ILIKE
        return {
            "busca": q,
            "resultados": [
                {"nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 0.8}
                for r in rows
            ]
        }
