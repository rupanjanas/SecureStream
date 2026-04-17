from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from openai import OpenAI
import os

# ✅ Load environment variables
load_dotenv()

# ✅ Get env variables
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ❗ Safety check
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set in .env")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in .env")

# ✅ Initialize DB
engine = create_engine(
    DATABASE_URL,
    connect_args={"sslmode": "require"}
)

# ✅ Initialize OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# ✅ FastAPI app
app = FastAPI()

# -------------------------------
# 📦 Models
# -------------------------------

class DocInput(BaseModel):
    org_id: str
    content: str

class QueryInput(BaseModel):
    org_id: str
    query: str

# -------------------------------
# 🔧 Helper: Get Embedding
# -------------------------------

def get_embedding(text: str):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

# -------------------------------
# 📥 Upload Document
# -------------------------------

@app.post("/upload")
def upload_doc(doc: DocInput):
    embedding = get_embedding(doc.content)

    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO documents (org_id, content, embedding)
                VALUES (:org_id, :content, :embedding)
            """),
            {
                "org_id": doc.org_id,
                "content": doc.content,
                "embedding": embedding
            }
        )
        conn.commit()

    return {"message": "Document stored successfully"}

# -------------------------------
# 🔍 Search Documents
# -------------------------------

def search_docs(org_id, query_embedding):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT content
                FROM documents
                WHERE org_id = :org_id
                ORDER BY embedding <-> :embedding
                LIMIT 5
            """),
            {
                "org_id": org_id,
                "embedding": query_embedding
            }
        )
        return [row[0] for row in result]

# -------------------------------
# 🤖 Query (RAG)
# -------------------------------

@app.post("/query")
def query_docs(q: QueryInput):
    query_embedding = get_embedding(q.query)

    docs = search_docs(q.org_id, query_embedding)
    context = "\n\n".join(docs)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "Answer only using the provided context. If not found, say you don't know."
            },
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion: {q.query}"
            }
        ]
    )

    return {
        "answer": response.choices[0].message.content,
        "sources": docs
    }

# -------------------------------
# 🧪 Health Check
# -------------------------------

@app.get("/")
def root():
    return {"status": "AI service running 🚀"}