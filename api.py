from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from psycopg2 import pool
import os, jwt, hashlib, re
from datetime import datetime, timedelta
from typing import List, Optional

app = FastAPI(title="Busca Vetorial - API Leve v3 Fix Contagem")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

SECRET_KEY = os.getenv("JWT_SECRET", "troque-essa-chave-mude-no-render")
ALGORITHM = "HS256"
DB_URL = os.getenv("DATABASE_URL")

db_pool = None
if DB_URL:
    try:
        db_pool = pool.SimpleConnectionPool(1, 5, dsn=DB_URL, keepalives=1, keepalives_idle=30, keepalives_interval=10)
    except Exception as e:
        print(f"Erro pool: {e}")

security = HTTPBearer(auto_error=False)

try:
    from sentence_transformers import SentenceTransformer
    emb_model = SentenceTransformer('all-MiniLM-L6-v2')
except:
    emb_model = None

def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def criar_token(dados):
    exp = datetime.utcnow() + timedelta(days=7)
    payload = {**dados, "exp": exp, "empresa_id": dados.get("empresa_id",1)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(401, "Not authenticated")
    try:
        return jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except:
        raise HTTPException(401, "Token invalido")

def require_role(roles: List[str]):
    def check(user=Depends(get_user)):
        if user.get("role") not in roles:
            raise HTTPException(403, f"Precisa ser {roles}")
        return user
    return check

class LoginRequest(BaseModel):
    email: str
    senha: str

@app.post("/login")
def login(req: LoginRequest):
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, email, role, empresa_id, senha_hash FROM usuarios_busca WHERE email=%s AND ativo=true", (req.email,))
        row = cur.fetchone()
        if not row or row[4] != hash_senha(req.senha):
            raise HTTPException(401, "Usuario ou senha incorreta")
        id_, email, role, empresa_id, _ = row
        token = criar_token({"sub": str(id_), "id": id_, "email": email, "role": role, "empresa_id": empresa_id or 1})
        return {"token": token, "role": role, "email": email, "empresa_id": empresa_id or 1}
    finally:
        db_pool.putconn(conn)

def extrair_termo_produto(q: str) -> str:
    # "quantos pacotes de arroz tem" -> "arroz"
    # "quantos pacotes de feijao tem" -> "feijao"
    q = q.lower()
    # remove prefixos de contagem
    q = re.sub(r'quantos?|quanto|total de|tem|existe|existem|pacotes?|unidades?|de', ' ', q)
    q = re.sub(r'\s+', ' ', q).strip()
    # se ficar vazio, retorna vazio
    if len(q) < 2:
        return ""
    # pega a ultima palavra relevante (geralmente o produto)
    # "pacotes de arroz" -> arroz
    partes = q.split()
    # ignora palavras muito genericas
    genericas = ["produtos","produto","coisas","itens","tudo"]
    for p in reversed(partes):
        if p not in genericas and len(p) > 2:
            return p
    return partes[-1] if partes else ""

@app.get("/buscar")
def buscar(q: str, user=Depends(require_role(["admin","gerente","analista"]))):
    if len(q) < 2:
        return {"busca": q, "resultados": [], "tempo_ms": 0}
    inicio = datetime.now()
    empresa_id = None if user["role"] == "admin" else user.get("empresa_id", 1)
    q_lower = q.lower().strip()
    
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        # === CASO 1: LISTAGEM GENERICA ===
        if q_lower in ["produtos","produto","todos","listar","listar produtos","mostrar tudo","mostrar produtos"]:
            sql = "SELECT id, nome, descricao, preco FROM produtos LIMIT 20" if empresa_id is None else "SELECT id, nome, descricao, preco FROM produtos WHERE empresa_id=%s LIMIT 20"
            cur.execute(sql, (empresa_id,) if empresa_id else ())
            rows = cur.fetchall()
            res = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 1.0} for r in rows]
            return {"busca": q, "tipo": "listagem", "resposta": f"Listando {len(res)} produtos", "resultados": res, "tempo_ms": int((datetime.now()-inicio).total_seconds()*1000), "modo": "lista"}

        # === CASO 2: CONTAGEM ===
        # "quantos produtos tem" ou "quantos pacotes de arroz tem"
        if "quanto" in q_lower:
            termo = extrair_termo_produto(q_lower)
            # se for contagem generica: "quantos produtos tem"
            if not termo or termo in ["produtos","produto"]:
                cur.execute("SELECT COUNT(*) FROM produtos" if empresa_id is None else "SELECT COUNT(*) FROM produtos WHERE empresa_id=%s", (empresa_id,) if empresa_id else ())
                total = cur.fetchone()[0]
                return {"busca": q, "tipo": "contagem", "resposta": f"Voce tem {total} produtos cadastrados no total.", "resultados": [], "tempo_ms": int((datetime.now()-inicio).total_seconds()*1000)}
            else:
                # contagem filtrada: "quantos pacotes de arroz tem" -> conta onde nome ILIKE %arroz%
                like = f"%{termo}%"
                if empresa_id is None:
                    cur.execute("SELECT COUNT(*), array_agg(nome) FROM produtos WHERE nome ILIKE %s OR descricao ILIKE %s", (like, like))
                else:
                    cur.execute("SELECT COUNT(*), array_agg(nome) FROM produtos WHERE empresa_id=%s AND (nome ILIKE %s OR descricao ILIKE %s)", (empresa_id, like, like))
                total, nomes = cur.fetchone()
                total = total or 0
                # tambem busca os produtos pra mostrar
                if empresa_id is None:
                    cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE nome ILIKE %s OR descricao ILIKE %s LIMIT 10", (like, like))
                else:
                    cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE empresa_id=%s AND (nome ILIKE %s OR descricao ILIKE %s) LIMIT 10", (empresa_id, like, like))
                rows = cur.fetchall()
                res = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 0.95} for r in rows]
                return {
                    "busca": q, 
                    "tipo": "contagem_filtrada", 
                    "resposta": f"Voce tem {total} produtos de '{termo}' cadastrados.",
                    "termo_extraido": termo,
                    "resultados": res, 
                    "tempo_ms": int((datetime.now()-inicio).total_seconds()*1000),
                    "modo": "contagem+lista"
                }

        # === CASO 3: BUSCA NORMAL VETORIAL COM FALLBACK ===
        resultados = []
        if emb_model:
            try:
                emb = emb_model.encode(q).tolist()
                if empresa_id is None:
                    cur.execute("SELECT id, nome, descricao, preco, 1 - (embedding <=> %s::vector) as score FROM produtos ORDER BY embedding <=> %s::vector LIMIT 20", (emb, emb))
                else:
                    cur.execute("SELECT id, nome, descricao, preco, 1 - (embedding <=> %s::vector) as score FROM produtos WHERE empresa_id=%s ORDER BY embedding <=> %s::vector LIMIT 20", (emb, empresa_id, emb))
                rows = cur.fetchall()
                # filtra score muito baixo (se nao tiver nada parecido, cai pro ILIKE)
                resultados = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": float(r[4])} for r in rows if float(r[4]) > 0.25]
                if resultados:
                    return {"busca": q, "tipo": "vetorial", "resultados": resultados, "tempo_ms": int((datetime.now()-inicio).total_seconds()*1000), "modo": "vetorial"}
            except Exception as e:
                print(f"vetorial falhou: {e}")
                conn.rollback()

        # fallback ILIKE
        like = f"%{q}%"
        if empresa_id is None:
            cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE nome ILIKE %s OR descricao ILIKE %s LIMIT 20", (like, like))
        else:
            cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE empresa_id=%s AND (nome ILIKE %s OR descricao ILIKE %s) LIMIT 20", (empresa_id, like, like))
        rows = cur.fetchall()
        resultados = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 0.8} for r in rows]
        return {"busca": q, "tipo": "texto", "resultados": resultados, "tempo_ms": int((datetime.now()-inicio).total_seconds()*1000), "modo": "texto-fallback"}

    finally:
        db_pool.putconn(conn)

@app.get("/health")
def health(): return {"status": "ok", "versao": "v3-contagem-inteligente"}

@app.get("/")
def root(): return {"message": "API v3 no ar"}
