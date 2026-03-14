"""
Qdrant query functions for the Drug Safety Check application.

Provides similarity search over adverse event reports and patient profiles
stored in Qdrant. Used by the integration layer (Part 5) to find similar
patients who experienced adverse events with a proposed drug.

Integrates DSC 202 course concepts:
- Cosine similarity for semantic matching (cos θ = ⟨x,y⟩ / (||x||·||y||))
- Dense vector search via HNSW index (approximate nearest neighbor)
- Payload-filtered search (narrowing candidates before vector comparison)
- Aspect-based analysis of adverse events (BERT_rev.py / aspect-based-sentiment.py)
- Embedding-based similarity analogous to Word2Vec/FastText word similarity
"""

import os
from collections import Counter
from functools import lru_cache

import numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    VectorParams,
    PointStruct,
)
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine

load_dotenv()

MODEL_NAME = "FremyCompany/BioLORD-2023"
VECTOR_DIM = 768  
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
ADVERSE_EVENTS_COLLECTION = "adverse_events"
PATIENT_PROFILES_COLLECTION = "patient_profiles"


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Load the sentence-transformer model (384-dim dense vectors).

    all-MiniLM-L6-v2 maps sentences to a 384-dimensional dense vector space.
    Chosen over Word2Vec/FastText because it captures full-sentence semantics
    rather than individual word embeddings (ref: DSC 202 lecture slide 13-14).
    Unlike Word2Vec (which produces one fixed vector per word), sentence-transformers
    produce context-aware embeddings similar to BERT but optimized for similarity
    tasks (ref: BERT_rev.py, lecture slide 20).
    """
    return SentenceTransformer(MODEL_NAME)


@lru_cache(maxsize=1)
def _get_client() -> QdrantClient:
    qdrant_path = os.getenv("QDRANT_PATH", "")
    if qdrant_path:
        return QdrantClient(path=qdrant_path)
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


def _embed(text: str) -> list[float]:
    model = _get_model()
    return model.encode(text).tolist()


def _embed_batch(texts: list[str]) -> np.ndarray:
    model = _get_model()
    return model.encode(texts, show_progress_bar=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_similar_adverse_events(
    patient_summary: str,
    drug_name: str,
    top_k: int = 10,
) -> list[dict]:
    """Find FAERS reports from similar patients who had adverse events with a drug.

    Uses cosine similarity in the 384-dim embedding space (ref: lecture slide 5):
        cos θ = ⟨query, report⟩ / (||query|| · ||report||)
    Payload filter on 'drug' narrows the HNSW search to only reports involving
    the specified drug before vector comparison (ref: lecture on Payload in Qdrant).

    Args:
        patient_summary: Free-text description of the patient.
        drug_name: The proposed drug to check adverse events for.
        top_k: Number of results to return.

    Returns:
        List of dicts sorted by similarity (highest first).
    """
    client = _get_client()
    query_vector = _embed(patient_summary)

    drug_filter = Filter(
        must=[
            FieldCondition(
                key="drug",
                match=MatchValue(value=drug_name.lower()),
            )
        ]
    )

    try:
        results = client.query_points(
            collection_name=ADVERSE_EVENTS_COLLECTION,
            query=query_vector,
            query_filter=drug_filter,
            limit=top_k,
        ).points
    except Exception:
        return []

    output = []
    for hit in results:
        payload = hit.payload or {}
        output.append({
            "patient_age": payload.get("patient_age"),
            "patient_sex": payload.get("patient_sex"),
            "drugs": payload.get("all_drugs", []),
            "reactions": payload.get("reactions", []),
            "outcome": payload.get("outcome", ""),
            "serious": payload.get("serious", False),
            "similarity_score": round(hit.score, 4),
            "raw_text": payload.get("raw_text", ""),
        })
    return output


def find_similar_adverse_events_multi_filter(
    patient_summary: str,
    drug_names: list[str] | None = None,
    outcome: str | None = None,
    serious_only: bool = False,
    sex: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Advanced filtered search combining multiple payload conditions.

    Demonstrates Qdrant's composable filter model (lecture: Payload in Qdrant).
    Each `must` condition narrows the candidate set via payload indexes before
    the HNSW traversal, which is critical for performance in high-dimensional
    spaces affected by the curse of dimensionality (lecture slides 9-10).

    Args:
        patient_summary: Free-text patient description to embed as query vector.
        drug_names: Filter to reports involving any of these drugs (MatchAny).
        outcome: Filter by outcome type (e.g. "hospitalization", "death").
        serious_only: If True, only return serious adverse events.
        sex: Filter by patient sex ("male" or "female").
        top_k: Number of results.
    """
    client = _get_client()
    query_vector = _embed(patient_summary)

    must_conditions = []
    if drug_names:
        must_conditions.append(
            FieldCondition(key="drug", match=MatchAny(any=[d.lower() for d in drug_names]))
        )
    if outcome:
        must_conditions.append(
            FieldCondition(key="outcome", match=MatchValue(value=outcome))
        )
    if serious_only:
        must_conditions.append(
            FieldCondition(key="serious", match=MatchValue(value=True))
        )
    if sex:
        must_conditions.append(
            FieldCondition(key="patient_sex", match=MatchValue(value=sex.lower()))
        )

    query_filter = Filter(must=must_conditions) if must_conditions else None

    try:
        results = client.query_points(
            collection_name=ADVERSE_EVENTS_COLLECTION,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
        ).points
    except Exception:
        return []

    return [
        {
            "patient_age": (hit.payload or {}).get("patient_age"),
            "patient_sex": (hit.payload or {}).get("patient_sex"),
            "drugs": (hit.payload or {}).get("all_drugs", []),
            "reactions": (hit.payload or {}).get("reactions", []),
            "outcome": (hit.payload or {}).get("outcome", ""),
            "serious": (hit.payload or {}).get("serious", False),
            "similarity_score": round(hit.score, 4),
            "raw_text": (hit.payload or {}).get("raw_text", ""),
        }
        for hit in results
    ]


def find_similar_patients(
    patient_summary: str,
    top_k: int = 10,
) -> list[dict]:
    """Find similar patient profiles from the Synthea dataset.

    Embeds the patient summary into the same 384-dim space and performs k-NN
    search over the patient_profiles collection using HNSW index.

    Args:
        patient_summary: Free-text description of the patient.
        top_k: Number of results to return.

    Returns:
        List of dicts sorted by similarity (highest first).
    """
    client = _get_client()
    query_vector = _embed(patient_summary)

    try:
        results = client.query_points(
            collection_name=PATIENT_PROFILES_COLLECTION,
            query=query_vector,
            limit=top_k,
        ).points
    except Exception:
        return []

    output = []
    for hit in results:
        payload = hit.payload or {}
        output.append({
            "patient_id": payload.get("patient_id", ""),
            "conditions": payload.get("conditions", []),
            "medications": payload.get("medications", []),
            "similarity_score": round(hit.score, 4),
        })
    return output


# ---------------------------------------------------------------------------
# Aspect-based adverse event analysis
# (ref: BERT_rev.py analyze_aspect_relationships, aspect-based-sentiment.py)
# ---------------------------------------------------------------------------

CLINICAL_ASPECTS = {
    "severity": {
        "high": ["death", "life-threatening", "hospitalization", "disability"],
        "moderate": ["serious", "required intervention", "congenital anomaly"],
        "low": ["non-serious", "recovered", "mild"],
    },
    "system_organ_class": {
        "cardiovascular": ["cardiac", "heart", "hypertension", "hypotension", "arrhythmia", "tachycardia"],
        "gastrointestinal": ["nausea", "vomiting", "diarrhoea", "abdominal", "constipation"],
        "neurological": ["headache", "dizziness", "seizure", "tremor", "neuropathy", "syncope"],
        "dermatological": ["rash", "pruritus", "urticaria", "skin", "alopecia"],
        "hepatic": ["hepatic", "liver", "jaundice", "hepatotoxicity"],
        "renal": ["renal", "kidney", "nephrotoxicity", "creatinine"],
    },
}


def analyze_adverse_event_aspects(results: list[dict]) -> dict:
    """Perform aspect-based analysis on adverse event search results.

    Inspired by aspect-based-sentiment.py and BERT_rev.py from DSC 202:
    instead of general sentiment (positive/negative), we classify adverse events
    by clinically meaningful aspects (severity, organ system) and quantify the
    distribution within each aspect.

    Args:
        results: Output from find_similar_adverse_events().

    Returns:
        Dict with aspect distributions and top reactions.
    """
    severity_counts = Counter()
    organ_counts = Counter()
    reaction_counts = Counter()
    outcome_counts = Counter()

    for r in results:
        outcome_counts[r.get("outcome", "unknown")] += 1
        for reaction in r.get("reactions", []):
            reaction_counts[reaction] += 1
            for organ, keywords in CLINICAL_ASPECTS["system_organ_class"].items():
                if any(kw in reaction.lower() for kw in keywords):
                    organ_counts[organ] += 1
                    break

        outcome_text = r.get("outcome", "").lower()
        classified = False
        for severity, keywords in CLINICAL_ASPECTS["severity"].items():
            if any(kw in outcome_text for kw in keywords):
                severity_counts[severity] += 1
                classified = True
                break
        if not classified:
            severity_counts["unknown"] += 1

    return {
        "total_reports": len(results),
        "severity_distribution": dict(severity_counts.most_common()),
        "organ_system_distribution": dict(organ_counts.most_common()),
        "top_reactions": dict(reaction_counts.most_common(10)),
        "outcome_distribution": dict(outcome_counts.most_common()),
    }


# ---------------------------------------------------------------------------
# Embedding similarity utilities
# (ref: Word2Vec.py calculate_word_similarity, FastText.py compare_partial_words)
# ---------------------------------------------------------------------------

def compute_drug_similarity(drug1: str, drug2: str) -> float:
    """Compute cosine similarity between two drug names in embedding space.

    Analogous to Word2Vec.py's calculate_word_similarity() and FastText.py's
    compare_partial_words(), but using sentence-transformer embeddings that
    capture richer semantic context than single-word vectors.

    Cosine similarity: cos θ = ⟨v1, v2⟩ / (||v1|| · ||v2||)  (lecture slide 5)
    """
    v1 = np.array(_embed(drug1)).reshape(1, -1)
    v2 = np.array(_embed(drug2)).reshape(1, -1)
    return float(sklearn_cosine(v1, v2)[0][0])


def compute_pairwise_drug_similarities(drug_names: list[str]) -> dict:
    """Compute pairwise cosine similarities for a set of drugs.

    Returns a dict of {(drug_a, drug_b): similarity_score} pairs.
    Analogous to Word2Vec.py's batch similarity analysis.
    """
    vectors = _embed_batch(drug_names)
    sim_matrix = sklearn_cosine(vectors)
    pairs = {}
    for i in range(len(drug_names)):
        for j in range(i + 1, len(drug_names)):
            pairs[(drug_names[i], drug_names[j])] = round(float(sim_matrix[i][j]), 4)
    return pairs


# ---------------------------------------------------------------------------
# Utility: load patient profiles into Qdrant (called from ETL or standalone)
# ---------------------------------------------------------------------------

def load_patient_profiles(profiles: list[dict]) -> int:
    """
    Embed and upsert Synthea patient profiles into Qdrant.

    Args:
        profiles: List of dicts from pg_queries.get_patient_profile(), each with:
            {patient_id, age, gender, conditions: [...], medications: [...]}

    Returns:
        Number of profiles loaded.
    """
    client = _get_client()
    model = _get_model()

    if not client.collection_exists(PATIENT_PROFILES_COLLECTION):
        client.create_collection(
            collection_name=PATIENT_PROFILES_COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )

    texts = []
    for p in profiles:
        age = p.get("age", "Unknown age")
        gender = p.get("gender", "unknown")
        conditions = ", ".join(p.get("conditions", [])) or "none"
        medications = ", ".join(p.get("medications", [])) or "none"
        texts.append(
            f"Patient: {age} year old {gender}. "
            f"Conditions: {conditions}. "
            f"Medications: {medications}."
        )

    vectors = model.encode(texts, show_progress_bar=True, batch_size=64)

    points = []
    for i, (profile, vec) in enumerate(zip(profiles, vectors)):
        points.append(
            PointStruct(
                id=i,
                vector=vec.tolist(),
                payload={
                    "patient_id": profile.get("patient_id", ""),
                    "age": profile.get("age"),
                    "gender": profile.get("gender", ""),
                    "conditions": profile.get("conditions", []),
                    "medications": profile.get("medications", []),
                },
            )
        )

    batch_size = 200
    for start in range(0, len(points), batch_size):
        client.upsert(
            collection_name=PATIENT_PROFILES_COLLECTION,
            points=points[start : start + batch_size],
        )

    return len(points)
