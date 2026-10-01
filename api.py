from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
from psycopg2 import pool
import os, jwt, hashlib
from datetime import datetime, timedelta
from typing import List, Optional

app = FastAPI(title="Busca Vetorial - API Leve FIX + Hibrida")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Modelo vetorial - carrega uma vez
try:
    from sentence_transformers import SentenceTransformer
    emb_model = SentenceTransformer('all-MiniLM-L6-v2')
    print("Modelo all-MiniLM-L6-v2 carregado")
except Exception as e:
    emb_model = None
    print(f"Sem modelo vetorial (vai usar ILIKE): {e}")

def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()

def criar_token(dados):
    exp = datetime.utcnow() + timedelta(days=7)
    payload = {**dados, "exp": exp}
    # garante campos essenciais
    if "empresa_id" not in payload:
        payload["empresa_id"] = 1
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

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
        cur.execute("SELECT id, email, role, empresa_id, senha_hash FROM usuarios_busca WHERE email=%s AND ativo=true", (req.email,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(401, "Usuario nao encontrado")
        id_, email, role, empresa_id, senha_hash = row
        if senha_hash != hash_senha(req.senha):
            raise HTTPException(401, "Senha incorreta")
        # payload novo com empresa_id e role - ESSENCIAL pro RLS futuro
        token = criar_token({
            "sub": str(id_),
            "id": id_,
            "email": email,
            "role": role,
            "empresa_id": empresa_id or 1
        })
        return {"token": token, "role": role, "email": email, "empresa_id": empresa_id or 1}
    finally:
        db_pool.putconn(conn)

def do_busca_texto(q: str, empresa_id: Optional[int], limit: int = 20):
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

PERGUNTAS_SQL = ["quantos", "quanto", "total", "soma", "média", "media", "mais caro", "mais barato", "lista todos", "quantos produtos"]

@app.get("/buscar_publica")
def buscar_publica(q: str):
    if len(q) < 2:
        return {"busca": q, "resultados": [], "tempo_ms": 0, "modo": "publico"}
    if not db_pool:
        return {"busca": q, "resultados": [], "tempo_ms": 0, "erro": "sem db"}
    resultados, tempo = do_busca_texto(q, None)
    return {"busca": q, "resultados": resultados, "tempo_ms": tempo, "modo": "publico-texto"}

@app.get("/buscar")
def buscar(q: str, user=Depends(require_role(["admin","gerente","analista"]))):
    if len(q) < 2:
        return {"busca": q, "resultados": [], "tempo_ms": 0, "tipo": "curta"}
    if not db_pool:
        raise HTTPException(500, "sem db")
    
    inicio = datetime.now()
    empresa_id = None if user["role"] == "admin" else user.get("empresa_id", 1)
    q_lower = q.lower().strip()

    conn = db_pool.getconn()
    try:
        cur = conn.cursor()
        # 1) ROTEADOR - contagem e agregacoes nao vao pro vetor
        if any(p in q_lower for p in PERGUNTAS_SQL):
            try:
                if "quantos produtos" in q_lower or q_lower in ["quantos produtos tem", "quantos produtos"]:
                    if empresa_id is None:
                        cur.execute("SELECT COUNT(*) FROM produtos")
                    else:
                        cur.execute("SELECT COUNT(*) FROM produtos WHERE empresa_id=%s", (empresa_id,))
                    total = cur.fetchone()[0]
                    tempo = int((datetime.now()-inicio).total_seconds()*1000)
                    # log
                    try:
                        cur.execute("INSERT INTO logs_busca (empresa_id, usuario_email, termo, resultados, tempo_ms, score_top) VALUES (%s,%s,%s,%s,%s,%s)",
                                    (user.get("empresa_id"), user["email"], q, total, tempo, 1.0))
                        conn.commit()
                    except:
                        conn.rollback()
                    return {"busca": q, "tipo": "contagem", "resposta": f"Voce tem {total} produtos cadastrados.", "resultados": [], "tempo_ms": tempo}
                
                if "mais caro" in q_lower:
                    if empresa_id is None:
                        cur.execute("SELECT id, nome, descricao, preco FROM produtos ORDER BY preco DESC LIMIT 1")
                    else:
                        cur.execute("SELECT id, nome, descricao, preco FROM produtos WHERE empresa_id=%s ORDER BY preco DESC LIMIT 1", (empresa_id,))
                    r = cur.fetchone()
                    res = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "score": 1.0}] if r else []
                    tempo = int((datetime.now()-inicio).total_seconds()*1000)
                    return {"busca": q, "tipo": "sql", "resposta": "Produto mais caro:", "resultados": res, "tempo_ms": tempo}
            except Exception as e:
                print(f"Erro roteador SQL: {e}")
                conn.rollback()

        # 2) TENTA HIBRIDO VETORIAL + TEXTO se modelo existir
        resultados = []
        modo = "texto"
        if emb_model is not None:
            try:
                query_embedding = emb_model.encode(q).tolist()
                # tenta hybrid, se tabela tiver busca_texto e embedding
                if empresa_id is None:
                    sql = """
                    WITH semantic AS (
                      SELECT id, 1 - (embedding <=> %s::vector) as score_sem
                      FROM produtos
                      ORDER BY embedding <=> %s::vector
                      LIMIT 20
                    ),
                    keyword AS (
                      SELECT id, ts_rank(busca_texto, plainto_tsquery('portuguese', %s)) as score_kw
                      FROM produtos
                      WHERE busca_texto @@ plainto_tsquery('portuguese', %s)
                      LIMIT 20
                    )
                    SELECT p.id, p.nome, p.descricao, p.preco,
                           COALESCE(s.score_sem,0) as semantic,
                           COALESCE(k.score_kw,0) as keyword,
                           (COALESCE(s.score_sem,0)*0.7 + COALESCE(k.score_kw,0)*0.3) as score_final
                    FROM produtos p
                    LEFT JOIN semantic s ON s.id=p.id
                    LEFT JOIN keyword k ON k.id=p.id
                    WHERE s.id IS NOT NULL OR k.id IS NOT NULL
                    ORDER BY score_final DESC LIMIT 20;
                    """
                    cur.execute(sql, (query_embedding, query_embedding, q, q))
                else:
                    sql = """
                    WITH semantic AS (
                      SELECT id, 1 - (embedding <=> %s::vector) as score_sem
                      FROM produtos
                      WHERE empresa_id=%s
                      ORDER BY embedding <=> %s::vector
                      LIMIT 20
                    ),
                    keyword AS (
                      SELECT id, ts_rank(busca_texto, plainto_tsquery('portuguese', %s)) as score_kw
                      FROM produtos
                      WHERE busca_texto @@ plainto_tsquery('portuguese', %s) AND empresa_id=%s
                      LIMIT 20
                    )
                    SELECT p.id, p.nome, p.descricao, p.preco,
                           COALESCE(s.score_sem,0) as semantic,
                           COALESCE(k.score_kw,0) as keyword,
                           (COALESCE(s.score_sem,0)*0.7 + COALESCE(k.score_kw,0)*0.3) as score_final
                    FROM produtos p
                    LEFT JOIN semantic s ON s.id=p.id
                    LEFT JOIN keyword k ON k.id=p.id
                    WHERE (s.id IS NOT NULL OR k.id IS NOT NULL) AND p.empresa_id=%s
                    ORDER BY score_final DESC LIMIT 20;
                    """
                    cur.execute(sql, (query_embedding, empresa_id, query_embedding, q, q, empresa_id, empresa_id))
                rows = cur.fetchall()
                resultados = [{"id": r[0], "nome": r[1], "descricao": r[2], "preco": float(r[3]) if r[3] else None, "semantic": float(r[4] or 0), "keyword": float(r[5] or 0), "score": float(r[6] or 0)} for r in rows]
                modo = "hibrido"
            except Exception as e:
                # fallback se nao tem coluna busca_texto ou embedding
                print(f"Fallback hibrido falhou, usando ILIKE: {e}")
                conn.rollback()
                resultados, _ = do_busca_texto(q, empresa_id)
                modo = "texto-fallback"
        else:
            resultados, _ = do_busca_texto(q, empresa_id)
            modo = "texto"

        tempo = int((datetime.now()-inicio).total_seconds()*1000)
        # log
        try:
            cur.execute("INSERT INTO logs_busca (empresa_id, usuario_email, termo, resultados, tempo_ms, score_top) VALUES (%s,%s,%s,%s,%s,%s)",
                        (user.get("empresa_id"), user["email"], q, len(resultados), tempo, resultados[0]["score"] if resultados else 0))
            conn.commit()
        except Exception as e:
            print(f"Erro log: {e}")
            conn.rollback()
        
        return {"busca": q, "resultados": resultados, "tempo_ms": tempo, "modo": modo, "tipo": "busca"}
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

@app.get("/health")
def health(): return {"status": "ok", "versao": "leve-hibrida-contagem", "db": "conectado" if db_pool else "sem DATABASE_URL", "modelo": "ok" if emb_model else "fallback-texto"}

@app.get("/")
def root(): return {"message": "API no ar - leve hibrida", "publica": "/buscar_publica?q=teste", "privada": "/buscar?q=teste (precisa token)", "docs": "/docs", "health": "/health"}
