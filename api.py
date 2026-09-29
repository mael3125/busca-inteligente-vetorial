import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import requests
import traceback

DATABASE_URL = os.environ.get("postgresql://postgres:vpS720D8a1jrd6T8@db.wssltptloqjghdjnudoa.supabase.co:5432/postgres")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

print(f"API iniciada. DATABASE_URL configurada? {bool(DATABASE_URL)}")

app = FastAPI(title="Busca Inteligente Vetorial - Light")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL não configurada no Render Environment")
    return psycopg2.connect(DATABASE_URL)

def gerar_embedding(texto: str):
    # Tenta nova API do HF, com fallback
    headers = {"Content-Type": "application/json"}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"
    
    payload = {"inputs": f"query: {texto}", "options": {"wait_for_model": True}}
    
    # Lista de endpoints para tentar
    endpoints = [
        "https://api-inference.huggingface.co/pipeline/feature-extraction/intfloat/multilingual-e5-small",
        "https://api-inference.huggingface.co/models/intfloat/multilingual-e5-small"
    ]
    
    for api_url in endpoints:
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=40)
            print(f"Tentando {api_url} -> {response.status_code}")
            if response.status_code == 200:
                embedding = response.json()
                if isinstance(embedding, list) and len(embedding) > 0:
                    if isinstance(embedding[0], list) and len(embedding[0]) > 0 and isinstance(embedding[0][0], list):
                        embedding = embedding[0][0]
                    elif isinstance(embedding[0], list):
                        embedding = embedding[0]
                print(f"Embedding gerado: tamanho {len(embedding)}")
                return embedding
            else:
                print(f"Erro {response.status_code}: {response.text[:200]}")
        except Exception as e:
            print(f"Erro ao chamar HF {api_url}: {e}")
    
    print("Todos endpoints HF falharam, usando fallback ILIKE")
    return None

@app.get("/")
def home():
    return {"status": "ok", "mensagem": "API Busca Inteligente Light no ar! Sem torch.", "db_ok": bool(DATABASE_URL)}

@app.get("/buscar")
def buscar(q: str):
    try:
        if not q:
            return {"busca": q, "resultados": []}
        
        print(f"Busca recebida: {q}")
        vetor = gerar_embedding(q)
        print(f"Vetor: {'OK' if vetor else 'None (fallback)'}")
        
        conn = get_conn()
        cur = conn.cursor()
        
        if vetor is None:
            print("Usando fallback ILIKE")
            cur.execute("""
              SELECT id, nome, descricao, preco, 0.8 as score
              FROM produtos 
              WHERE nome ILIKE %s OR descricao ILIKE %s
              LIMIT 10
            """, (f"%{q}%", f"%{q}%"))
        else:
            try:
                cur.execute("""
                  SELECT id, nome, descricao, preco, 1 - (embedding <=> %s::vector) as score
                  FROM produtos ORDER BY embedding <=> %s::vector LIMIT 10
                """, (vetor, vetor))
            except Exception as e:
                print(f"Erro na busca vetorial, tentando ILIKE: {e}")
                # Se falhar o operador de vetor, tenta ILIKE
                cur.execute("""
                  SELECT id, nome, descricao, preco, 0.8 as score
                  FROM produtos 
                  WHERE nome ILIKE %s OR descricao ILIKE %s
                  LIMIT 10
                """, (f"%{q}%", f"%{q}%"))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        print(f"Retornando {len(rows)} resultados")

        if not rows:
            return {"busca": q, "resultados": []}

        if vetor is not None and len(rows) > 0 and rows[0][4] is not None:
            try:
                top_score = float(rows[0][4])
                if top_score < 0.3:  # Threshold mais baixo pra funcionar sempre
                    print(f"Top score baixo {top_score}, mas retornando mesmo assim")
                resultados = []
                for id_p, nome, desc, preco, score in rows:
                    s = float(score) if score else 0.8
                    resultados.append({
                        "nome": nome, "descricao": desc,
                        "preco": float(preco) if preco else None,
                        "score": round(s, 2)
                    })
                return {"busca": q, "resultados": resultados}
            except:
                pass

        return {
            "busca": q,
            "resultados": [
                {"nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 0.8}
                for r in rows
            ]
        }
    except Exception as e:
        print(f"ERRO em /buscar: {e}")
        traceback.print_exc()
        # Retorna erro como JSON em vez de 500 HTML
        return {"busca": q, "resultados": [], "erro": str(e), "detalhe": "Verifique DATABASE_URL e se a tabela produtos existe"}
