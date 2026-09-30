from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2 import pool
import os, jwt, hashlib
from datetime import datetime, timedelta
from typing import List, Optional

app = FastAPI(title="Busca Vetorial - API Leve FIX")

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

security = HTTPBearer(auto_error=False)

def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def criar_token(dados):
    exp = datetime.utcnow() + timedelta(hours=8)
    return jwt.encode({**dados, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def get_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if not credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except:
        return None

def get_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(401, "Not authenticated - faca login em /login")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except:
        raise HTTPException(401, "Token invalido ou expirado")

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
        raise HTTPException(500, "DATABASE_URL nao configurada")
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT email, role, empresa_id, senha_hash FROM usuarios_busca WHERE email=%s AND ativo=true", (req.email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Usuario nao encontrado")
        if row[3] != hash_senha(req.senha):
            raise HTTPException(401, "Senha incorreta")
        email, role, empresa_id, _ = row
        token = criar_token({"email": email, "role": role, "empresa_id": empresa_id or 1})
        return {"token": token, "role": role, "email": email, "empresa_id": empresa_id or 1}
    finally:
        db_pool.putconn(conn)

def do_busca(q: str, empresa_id: Optional[int], limit: int = 20):
    inicio = datetime.now()
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        like = f"%{q}%"
        if empresa_id is None:
            cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE nome ILIKE %s OR descricao ILIKE %s LIMIT %s", (like, like, limit))
        else:
            cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE empresa_id=%s AND (nome ILIKE %s OR descricao ILIKE %s) LIMIT %s", (empresa_id, like, like, limit))
        resultados = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 0.85} for r in cur.fetchall()]
        tempo = int((datetime.now()-inicio).total_seconds()*1000)
        return resultados, tempo
    finally:
        db_pool.putconn(conn)

# ENDPOINT PUBLICO - para o site github.io - SEM LOGIN
@app.get("/buscar_publica")
def buscar_publica(q: str):
    if len(q) < 2:
        return {"busca": q, "resultados": [], "tempo_ms": 0, "modo": "publico"}
    if not db_pool:
        return {"busca": q, "resultados": [], "tempo_ms": 0, "erro": "sem db"}
    resultados, tempo = do_busca(q, None)
    return {"busca": q, "resultados": resultados, "tempo_ms": tempo, "modo": "publico-texto"}

# ENDPOINT COM LOGIN - para painel admin
@app.get("/buscar")
def buscar(q: str, user=Depends(require_role(["admin","gerente","analista"]))):
    if len(q) < 2:
        return {"busca": q, "resultados": [], "tempo_ms": 0}
    resultados, tempo = do_busca(q, user["empresa_id"] if user["role"]!="admin" else None)
    # log
    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO logs_busca (empresa_id, usuario_email, termo, resultados, tempo_ms, score_top) VALUES (%s,%s,%s,%s,%s,%s)",
                        (user["empresa_id"], user["email"], q, len(resultados), tempo, 0.85))
            conn.commit()
        except:
            conn.rollback()
    finally:
        db_pool.putconn(conn)
    return {"busca": q, "resultados": resultados, "tempo_ms": tempo, "modo": "privado"}

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

@app.get("/health")
def health(): return {"status": "ok", "versao": "fix-publica-privada", "db": "conectado" if db_pool else "sem DATABASE_URL"}

@app.get("/")
def root(): return {"message": "API no ar", "publica": "/buscar_publica?q=teste", "privada": "/buscar?q=teste (precisa token)", "docs": "/docs"}
