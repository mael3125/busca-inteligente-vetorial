"""
API LEVE CORRIGIDA - Render Free
Fix: HTTPCredentials -> HTTPAuthorizationCredentials
"""

from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2 import pool
import os, jwt, hashlib
from datetime import datetime, timedelta
from typing import List

app = FastAPI(title="Busca Vetorial - API Leve Render")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

SECRET_KEY = os.getenv("JWT_SECRET", "troque-essa-chave-mude-no-render")
ALGORITHM = "HS256"
DB_URL = os.getenv("DATABASE_URL")

db_pool = None
if DB_URL:
    try:
        db_pool = pool.SimpleConnectionPool(1, 5, dsn=DB_URL, keepalives=1, keepalives_idle=30)
    except Exception as e:
        print(f"Erro pool: {e}")

security = HTTPBearer()

def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def criar_token(dados):
    exp = datetime.utcnow() + timedelta(hours=8)
    return jwt.encode({**dados, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def get_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception as e:
        raise HTTPException(401, f"Token inválido: {e}")

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
    if not db_pool:
        raise HTTPException(500, "DATABASE_URL não configurada no Render")
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT email, role, empresa_id, senha_hash FROM usuarios_busca WHERE email=%s AND ativo=true", (req.email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Usuário não encontrado")
        if row[3] != hash_senha(req.senha):
            raise HTTPException(401, "Senha incorreta")
        email, role, empresa_id, _ = row
        token = criar_token({"email": email, "role": role, "empresa_id": empresa_id or 1})
        return {"token": token, "role": role, "email": email, "empresa_id": empresa_id or 1}
    finally:
        db_pool.putconn(conn)

@app.get("/buscar")
def buscar(q: str, user=Depends(require_role(["admin","gerente","analista"]))):
    inicio = datetime.now()
    if len(q) < 2:
        return {"busca": q, "resultados": [], "tempo_ms": 0}
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        like = f"%{q}%"
        if user["role"] == "admin":
            cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE nome ILIKE %s OR descricao ILIKE %s LIMIT 20", (like, like))
        else:
            cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE empresa_id=%s AND (nome ILIKE %s OR descricao ILIKE %s) LIMIT 20", (user["empresa_id"], like, like))
        resultados = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 0.85} for r in cur.fetchall()]
        tempo = int((datetime.now()-inicio).total_seconds()*1000)
        try:
            cur.execute("INSERT INTO logs_busca (empresa_id, usuario_email, termo, resultados, tempo_ms, score_top) VALUES (%s,%s,%s,%s,%s,%s)",
                        (user["empresa_id"], user["email"], q, len(resultados), tempo, 0.85))
            conn.commit()
        except:
            conn.rollback()
        return {"busca": q, "resultados": resultados, "tempo_ms": tempo, "modo": "texto-leve"}
    finally:
        db_pool.putconn(conn)

@app.get("/admin/stats")
def stats(user=Depends(require_role(["admin"]))):
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM logs_busca WHERE created_at::date = CURRENT_DATE")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM logs_busca WHERE created_at::date = CURRENT_DATE AND resultados=0")
        zeradas = cur.fetchone()[0]
        cur.execute("SELECT AVG(tempo_ms) FROM logs_busca WHERE created_at::date = CURRENT_DATE")
        tempo = cur.fetchone()[0] or 0
        return {"total_hoje": total, "zeradas": zeradas, "tempo_medio": int(tempo), "score_medio": 0.85}
    finally:
        db_pool.putconn(conn)

@app.get("/admin/empresas")
def empresas(user=Depends(require_role(["admin"]))):
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT empresa_id, COUNT(DISTINCT usuario_email), COUNT(*) FILTER (WHERE created_at::date=CURRENT_DATE), COUNT(*) FILTER (WHERE created_at::date=CURRENT_DATE AND resultados=0) FROM logs_busca GROUP BY empresa_id")
        rows = cur.fetchall()
        if not rows:
            return [{"id": 1, "nome": "Empresa Demo", "usuarios": 2, "buscas_hoje": 0, "zeradas_hoje": 0, "status": "saudavel"}]
        return [{"id": r[0], "nome": f"Empresa {r[0]}", "usuarios": r[1], "buscas_hoje": r[2], "zeradas_hoje": r[3], "status": "saudavel" if r[3]<10 else "atencao"} for r in rows]
    finally:
        db_pool.putconn(conn)

@app.get("/admin/termos-zerados")
def termos_zerados(user=Depends(require_role(["admin","gerente"]))):
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        if user["role"] == "admin":
            cur.execute("SELECT termo, empresa_id, COUNT(*) as vezes, MAX(created_at) FROM logs_busca WHERE resultados=0 AND created_at > NOW() - INTERVAL '7 days' GROUP BY termo, empresa_id ORDER BY vezes DESC LIMIT 50")
        else:
            cur.execute("SELECT termo, empresa_id, COUNT(*) as vezes, MAX(created_at) FROM logs_busca WHERE resultados=0 AND empresa_id=%s AND created_at > NOW() - INTERVAL '7 days' GROUP BY termo, empresa_id ORDER BY vezes DESC LIMIT 50", (user["empresa_id"],))
        return [{"termo": r[0], "empresa_id": r[1], "vezes": r[2], "ultima": r[3].isoformat() if r[3] else ""} for r in cur.fetchall()]
    finally:
        db_pool.putconn(conn)

@app.get("/admin/logs")
def logs(limit: int = 100, user=Depends(require_role(["admin","gerente"]))):
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        if user["role"] == "admin":
            cur.execute("SELECT created_at, empresa_id, usuario_email, termo, resultados, tempo_ms FROM logs_busca ORDER BY created_at DESC LIMIT %s", (limit,))
        else:
            cur.execute("SELECT created_at, empresa_id, usuario_email, termo, resultados, tempo_ms FROM logs_busca WHERE empresa_id=%s ORDER BY created_at DESC LIMIT %s", (user["empresa_id"], limit))
        return [{"hora": r[0].isoformat(), "empresa_id": r[1], "usuario": r[2], "termo": r[3], "resultados": r[4], "tempo_ms": r[5]} for r in cur.fetchall()]
    finally:
        db_pool.putconn(conn)

@app.get("/health")
def health(): return {"status": "ok", "versao": "leve-render-free-corrigida", "db": "conectado" if db_pool else "sem DATABASE_URL"}

@app.get("/")
def root(): return {"message": "API Busca Vetorial Leve no ar", "docs": "/docs", "health": "/health"}
