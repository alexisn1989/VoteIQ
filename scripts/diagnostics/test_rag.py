#!/usr/bin/env python3
"""Test RAG queries against the voteiq_bills ChromaDB collection."""
import os
import chromadb
import voyageai
from dotenv import load_dotenv

load_dotenv()

COLLECTION_NAME = "voteiq_bills"
EMBED_MODEL     = "voyage-law-2"

def test_rag():
    """Run sample queries and display results."""
    client = chromadb.HttpClient(
        ssl=True,
        host="api.trychroma.com",
        tenant=os.getenv("CHROMA_TENANT"),
        database=os.getenv("CHROMA_DATABASE"),
        headers={"x-chroma-token": os.getenv("CHROMA_API_KEY")},
    )

    collection = client.get_collection(COLLECTION_NAME)
    print(f"✓ Connected to collection '{COLLECTION_NAME}'")
    print(f"  Total documents: {collection.count()}\n")

    vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))

    queries = [
        "What bills about healthcare did Senator Surovell sponsor?",
        "Education funding bills passed in House",
        "Bills that failed in committee",
        "Wage and labor bills",
    ]

    for query in queries:
        print(f"\n{'='*70}")
        print(f"Query: {query}")
        print(f"{'='*70}")

        try:
            query_embedding = vo.embed([query], model=EMBED_MODEL, input_type="query").embeddings[0]
            results = collection.query(query_embeddings=[query_embedding], n_results=3)

            for i, (doc, meta, dist) in enumerate(zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]
            ), 1):
                score = 1 - dist  # Convert distance to similarity score
                print(f"\n  Result {i} [{meta.get('chunk_type', 'unknown')}] (score: {score:.3f})")
                print(f"  Bill: {meta.get('bill_id', 'N/A')} | Session: {meta.get('session', 'N/A')}")
                if meta.get('primary_sponsor'):
                    print(f"  Sponsor: {meta.get('primary_sponsor')}")
                print(f"  {doc[:200]}...")
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == "__main__":
    test_rag()
