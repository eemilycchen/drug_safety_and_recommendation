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
    """Load BioLORD-2023 sentence-transformer model (768-dim dense vectors).
    FremyCompany/BioLORD-2023 maps clinical text to a 768-dimensional space.
    Trained on UMLS, SNOMED CT, MedDRA, and PubMed for biomedical similarity.
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

    Uses cosine similarity in the 768-dim embedding space (ref: lecture slide 5):
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

    Embeds the patient summary into the same 768-dim space and performs k-NN
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

# ── Drug profiles + safe alternatives pipeline ──────────────────────────────

DRUG_PROFILES_COLLECTION = "drug_profiles"

# ---------------------------------------------------------------------------
# Drug profile text builder
# ---------------------------------------------------------------------------

def _build_drug_profile_text(drug_name: str, drug_class: str = "",
                              mechanism: str = "", conditions: list[str] = None,
                              side_effects: list[str] = None) -> str:
    """
    Convert drug metadata into a rich text string for embedding.
    The richer the text, the better BioLORD can place it in semantic space.

    Even with just a drug name, BioLORD already knows a lot —
    but adding class + mechanism makes similarity much more precise.
    """
    conditions_str   = ", ".join(conditions or []) or "various conditions"
    side_effects_str = ", ".join(side_effects or []) or "see label"
    parts = [f"Drug: {drug_name}."]
    if drug_class:
        parts.append(f"Class: {drug_class}.")
    if mechanism:
        parts.append(f"Mechanism: {mechanism}.")
    parts.append(f"Used for: {conditions_str}.")
    parts.append(f"Common side effects: {side_effects_str}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Load drug profiles into Qdrant
# (call this once from your ETL — similar to load_patient_profiles)
# ---------------------------------------------------------------------------

def load_drug_profiles(drug_list: list[dict]) -> int:
    """
    Embed and upsert drug profiles into Qdrant drug_profiles collection.

    Each drug dict should have:
        {
            "name":        "warfarin",           # required
            "drug_class":  "anticoagulant",      # optional but improves similarity
            "mechanism":   "vitamin K antagonist",# optional
            "conditions":  ["atrial fibrillation", "DVT"],  # optional
            "side_effects":["bleeding", "bruising"],        # optional
        }

    Returns number of profiles loaded.

    Example usage in ETL:
        from db.qdrant_queries import load_drug_profiles
        drugs = [
            {"name": "warfarin",   "drug_class": "anticoagulant",  "conditions": ["atrial fibrillation"]},
            {"name": "apixaban",   "drug_class": "anticoagulant",  "conditions": ["atrial fibrillation"]},
            {"name": "aspirin",    "drug_class": "NSAID",          "conditions": ["pain", "heart attack prevention"]},
            {"name": "ibuprofen",  "drug_class": "NSAID",          "conditions": ["pain", "inflammation"]},
            {"name": "metformin",  "drug_class": "antidiabetic",   "conditions": ["type 2 diabetes"]},
            {"name": "insulin",    "drug_class": "antidiabetic",   "conditions": ["type 1 diabetes", "type 2 diabetes"]},
        ]
        n = load_drug_profiles(drugs)
    """
    client = _get_client()
    model  = _get_model()

    vector_dim = model.get_sentence_embedding_dimension()

    if not client.collection_exists(DRUG_PROFILES_COLLECTION):
        client.create_collection(
            collection_name=DRUG_PROFILES_COLLECTION,
            vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE),
        )

    texts = [
        _build_drug_profile_text(
            drug_name    = d.get("name", ""),
            drug_class   = d.get("drug_class", ""),
            mechanism    = d.get("mechanism", ""),
            conditions   = d.get("conditions", []),
            side_effects = d.get("side_effects", []),
        )
        for d in drug_list
    ]

    vectors = model.encode(texts, show_progress_bar=False, batch_size=64)

    points = [
        PointStruct(
            id      = i,
            vector  = vectors[i].tolist(),
            payload = {
                "name":        drug_list[i].get("name", ""),
                "drug_class":  drug_list[i].get("drug_class", ""),
                "mechanism":   drug_list[i].get("mechanism", ""),
                "conditions":  drug_list[i].get("conditions", []),
                "side_effects":drug_list[i].get("side_effects", []),
                "profile_text":texts[i],
            },
        )
        for i in range(len(drug_list))
    ]

    batch_size = 200
    for start in range(0, len(points), batch_size):
        client.upsert(
            collection_name=DRUG_PROFILES_COLLECTION,
            points=points[start : start + batch_size],
        )

    return len(points)


# ---------------------------------------------------------------------------
# find_similar_drugs — Step 1 of Qdrant → Neo4j pipeline
# ---------------------------------------------------------------------------

def find_similar_drugs(
    proposed_drug: str,
    top_k: int = 10,
    exclude_drug: str | None = None,
) -> list[dict]:
    """
    Find drugs semantically similar to proposed_drug using BioLORD embeddings.

    This is Step 1 of the safe alternatives pipeline:
        Qdrant (find candidates) → Neo4j (filter unsafe ones)

    Args:
        proposed_drug: Drug name to find alternatives for (e.g. "warfarin")
        top_k:         Number of candidates to return (default 10)
        exclude_drug:  Drug name to exclude from results (usually proposed_drug
                       itself — Qdrant may return it as its own nearest neighbour)

    Returns:
        List of dicts sorted by similarity (highest first), each with:
            {
                "name":             "apixaban",
                "drug_class":       "anticoagulant",
                "mechanism":        "factor Xa inhibitor",
                "conditions":       ["atrial fibrillation"],
                "side_effects":     ["bleeding"],
                "similarity_score": 0.847,
            }

    Example:
        candidates = find_similar_drugs("warfarin", top_k=10)
        # → [{"name": "apixaban", "similarity_score": 0.85}, ...]

        # Then pass to Neo4j:
        safe = check_interactions(patient_meds, [c["name"] for c in candidates])
    """
    client       = _get_client()
    query_vector = _embed(proposed_drug)

    # If drug_profiles collection doesn't exist yet, return empty with clear message
    if not client.collection_exists(DRUG_PROFILES_COLLECTION):
        return []

    try:
        # fetch top_k + 1 so we can exclude the drug itself if it appears
        results = client.query_points(
            collection_name=DRUG_PROFILES_COLLECTION,
            query=query_vector,
            limit=top_k + 1,
        ).points
    except Exception:
        return []

    output = []
    for hit in results:
        payload = hit.payload or {}
        name    = payload.get("name", "")

        # skip the drug itself — Qdrant may return it as its own nearest neighbour
        if exclude_drug and name.lower() == exclude_drug.lower():
            continue
        if name.lower() == proposed_drug.lower():
            continue

        output.append({
            "name":             name,
            "drug_class":       payload.get("drug_class", ""),
            "mechanism":        payload.get("mechanism", ""),
            "conditions":       payload.get("conditions", []),
            "side_effects":     payload.get("side_effects", []),
            "similarity_score": round(hit.score, 4),
        })

        if len(output) >= top_k:
            break

    return output


# ---------------------------------------------------------------------------
# find_safe_alternatives — full Qdrant → Neo4j pipeline (use in Part 5)
# ---------------------------------------------------------------------------

def find_safe_alternatives_candidates(
    proposed_drug: str,
    top_k: int = 10,
) -> dict:
    """
    Stage 1 of the safe alternatives pipeline.
    Returns candidates for Neo4j to filter.

    Part 5 calls this, then passes results to Neo4j check_interactions().

    Returns:
        {
            "proposed_drug":  "warfarin",
            "candidates":     [{"name": "apixaban", "similarity_score": 0.85}, ...],
            "total_found":    8,
            "status":         "ok" | "empty" | "collection_missing"
        }

    Part 5 usage:
        # Step 1 — Qdrant
        result     = find_safe_alternatives_candidates("warfarin", top_k=10)
        candidates = [c["name"] for c in result["candidates"]]

        # Step 2 — Neo4j filters
        safe = check_interactions(patient_meds, candidates)

        # Step 3 — escalate if needed
        if not safe:
            # expand to top 25
            result   = find_safe_alternatives_candidates("warfarin", top_k=25)
            candidates = [c["name"] for c in result["candidates"]]
            safe     = check_interactions(patient_meds, candidates)
    """
    if not _get_client().collection_exists(DRUG_PROFILES_COLLECTION):
        return {
            "proposed_drug": proposed_drug,
            "candidates":    [],
            "total_found":   0,
            "status":        "collection_missing",
            "message":       (
                "drug_profiles collection not found. "
                "Run load_drug_profiles() first."
            ),
        }

    candidates = find_similar_drugs(proposed_drug, top_k=top_k)

    return {
        "proposed_drug": proposed_drug,
        "candidates":    candidates,
        "total_found":   len(candidates),
        "status":        "ok" if candidates else "empty",
        "message":       (
            f"Found {len(candidates)} candidates for Neo4j to filter."
            if candidates else
            f"No similar drugs found for '{proposed_drug}'. "
            "Add more drugs to drug_profiles collection."
        ),
    }
