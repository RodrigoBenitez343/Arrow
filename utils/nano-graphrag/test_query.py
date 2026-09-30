import os
import asyncio
import logging

import numpy as np
from sentence_transformers import SentenceTransformer

from nano_graphrag import GraphRAG, QueryParam
from nano_graphrag._utils import (
    compute_args_hash,
    wrap_embedding_func_with_attrs,
    logger,
)
from nano_graphrag.base import BaseKVStorage

logging.basicConfig(level=logging.WARNING)
logging.getLogger("nano-graphrag").setLevel(logging.INFO)

# ─── Configuration ──────────────────────────────────────────
MODEL_PATH = "models\SmolLM3-Q4_K_M.gguf"
# Alternative: MODEL_PATH = "./models/SmolLM3-Q4_K_M.gguf"

WORKING_DIR = "./my_local_cache"
LLAMA_CLI = "llama-cli"

# Sentence-transformers model for embeddings (tiny, fast, ~80MB)
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384
EMBED_MAX_TOKENS = 8192


# ─── Load embedding model only (LLM via llama-cli) ─────────
print("Loading embedding model...")
embedder = SentenceTransformer(EMBED_MODEL_NAME)


# ─── LLM via llama-cli subprocess ──────────────────────────
def _build_prompt_text(prompt, system_prompt=None, history_messages=[]) -> str:
    parts = []
    if system_prompt:
        parts.append(f"System: {system_prompt}")
    for msg in history_messages:
        role = msg.get("role", "user").capitalize()
        parts.append(f"{role}: {msg.get('content', '')}")
    parts.append(f"User: {prompt}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


async def local_llm(prompt, system_prompt=None, history_messages=[], **kwargs) -> str:
    hashing_kv: BaseKVStorage = kwargs.get("hashing_kv")
    full_prompt = _build_prompt_text(prompt, system_prompt, history_messages)

    # Check cache
    if hashing_kv is not None:
        args_hash = compute_args_hash(MODEL_PATH, full_prompt)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached is not None:
            return cached["return"]

    max_tokens = kwargs.get("max_tokens", 1024)
    temperature = kwargs.get("temperature", 0.2)

    # Run llama-cli as subprocess
    proc = await asyncio.create_subprocess_exec(
        LLAMA_CLI,
        "-m", MODEL_PATH,
        "--prompt", full_prompt,
        "-n", str(max_tokens),
        "--temp", str(temperature),
        "--ctx-size", "4096",
        "--no-display-prompt",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    result = stdout.decode("utf-8", errors="replace").strip()

    # Save to cache
    if hashing_kv is not None:
        await hashing_kv.upsert({args_hash: {"return": result, "model": MODEL_PATH}})
    return result


# ─── Embedding function for nano-graphrag ───────────────────
@wrap_embedding_func_with_attrs(
    embedding_dim=EMBED_DIM,
    max_token_size=EMBED_MAX_TOKENS,
)
async def local_embedding(texts: list[str]) -> np.ndarray:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: embedder.encode(texts))


# ─── Main ───────────────────────────────────────────────────
async def main():
    # Skip the OPENAI_API_KEY check since we're fully local
    os.environ["OPENAI_API_KEY"] = "not-needed"

    rag = GraphRAG(
        working_dir=WORKING_DIR,
        best_model_func=local_llm,
        cheap_model_func=local_llm,
        embedding_func=local_embedding,
        best_model_max_token_size=4096,
        cheap_model_max_token_size=4096,
        # Smaller chunk size for local models with limited context
        chunk_token_size=500,
        chunk_overlap_token_size=30,
        # Limit entity extraction to avoid overloading the small model
        entity_extract_max_gleaning=0,
    )

    # 1. Insert some text
    with open("./tests/mock_data.txt", encoding="utf-8-sig") as f:
        text = f.read()
    print("\nInserting data... (this will take a while on first run)")
    await rag.ainsert(text[:5000])  # Use a small portion for first test

    # 2. Query
    print("\n" + "=" * 50)
    print("Querying...")
    print(await rag.aquery("Dickens", param=QueryParam(mode="local")))


if __name__ == "__main__":
    asyncio.run(main())
