import asyncio
import json
import httpx
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_recall, context_precision
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.run_config import RunConfig
from langchain_groq import ChatGroq
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from datasets import Dataset
from test_questions import test_set
import os
from dotenv import load_dotenv


BASE_URL = "http://127.0.0.1:8000"
TOKEN = "dev-token"
load_dotenv(dotenv_path="../.env")  # loads from ai-service/.env
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


async def get_answer(question: str) -> dict:
    full_answer = ""
    sources = []
    source_passages = []

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/query/stream",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "question": question,
                "top_k":    5
            }
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("token"):
                            full_answer += data["token"]
                        if data.get("done"):
                            sources = data.get("sources", [])
                            source_passages = data.get("source_passages", [])
                    except Exception:
                        continue

    return {
        "answer":          full_answer.strip(),
        "sources":         sources,
        "source_passages": source_passages
    }


async def collect_results():
    results = []
    for item in test_set:
        print(f"⏳ Asking: {item['question'][:60]}...")
        try:
            response = await get_answer(item["question"])
            results.append({
                "question":     item["question"],
                "answer":       response["answer"],
                "contexts":     [p["passage"] for p in response["source_passages"]],
                "ground_truth": item["ground_truth"]
            })
            print(f"✅ Done — answer length: {len(response['answer'])} chars")
        except Exception as e:
            print(f"❌ Failed: {e}")
            results.append({
                "question":     item["question"],
                "answer":       "Error",
                "contexts":     [],
                "ground_truth": item["ground_truth"]
            })
    return results


# ── Collect answers from SecureStream ────────────────────────────────────────
print("\n🚀 Collecting answers from SecureStream...\n")
results = asyncio.run(collect_results())

print(f"\n✅ Collected {len(results)} results. Running RAGAS evaluation...\n")

# ── Filter out empty answers ──────────────────────────────────────────────────
valid_results = [
    r for r in results
    if r["answer"] and r["answer"] != "Error" and r["contexts"]
]
print(f"📊 Valid results for evaluation: {len(valid_results)}/{len(results)}")

if not valid_results:
    print("❌ No valid results. Check document is ingested and server is running.")
    exit(1)

# ── Build dataset ─────────────────────────────────────────────────────────────
data = {
    "question":     [r["question"]     for r in valid_results],
    "answer":       [r["answer"]       for r in valid_results],
    "contexts":     [r["contexts"]     for r in valid_results],
    "ground_truth": [r["ground_truth"] for r in valid_results]
}

dataset = Dataset.from_dict(data)

# ── RAGAS evaluation with Groq + FastEmbed ────────────────────────────────────
print("\n🤖 Setting up Groq LLM and FastEmbed embeddings...\n")

groq_llm = LangchainLLMWrapper(ChatGroq(
    model="llama-3.3-70b-versatile",   # ← better model = higher faithfulness
    api_key=GROQ_API_KEY,
    request_timeout=120,
    max_retries=3
))

groq_embeddings = LangchainEmbeddingsWrapper(FastEmbedEmbeddings())

# ── Run evaluation ────────────────────────────────────────────────────────────
print("⚙️  Running RAGAS evaluation — this may take 10-15 minutes...\n")

scores = evaluate(
    dataset,
    metrics=[
        faithfulness,
        answer_relevancy,
        context_recall,
        context_precision
    ],
    llm=groq_llm,
    embeddings=groq_embeddings,
    raise_exceptions=False,
    run_config=RunConfig(
        max_workers=2,
        max_retries=5,
        timeout=180,
    )
)

# ── Print and save results ────────────────────────────────────────────────────
print("\n📊 RAGAS Scores:")
print(scores)

df = scores.to_pandas()
df.to_csv("ragas_results.csv", index=False)
print("\n✅ Results saved to ragas_results.csv")

# ── Print per-question breakdown ──────────────────────────────────────────────
print("\n📋 Per-question breakdown:")
print(df[["question", "faithfulness", "answer_relevancy",
          "context_recall", "context_precision"]].to_string(index=False))