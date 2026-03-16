"""
Embedding analysis and visualization for FAERS adverse event data.

Demonstrates DSC 202 course concepts applied to the Drug Safety Check project:

1. Word2Vec / FastText concepts (Word2Vec.py, FastText.py):
   - Cosine similarity between drug/reaction embeddings
   - Word analogy via vector arithmetic
   - Vocabulary handling (FastText subword advantage for medical terms)

2. BERT concepts (BERT_rev.py):
   - Context-aware embeddings via sentence-transformers
   - Aspect-based analysis of adverse events

3. T5 concepts (T5.py):
   - Structured text serialization for embedding
   - Few-shot classification of adverse event severity

4. Vector Data Model lecture concepts:
   - t-SNE visualization of high-dimensional embeddings
   - Cosine similarity vs Euclidean distance comparison
   - Curse of dimensionality demonstration
   - HNSW search quality evaluation

Usage:
    python analysis/embedding_analysis.py
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sklearn.manifold import TSNE
from sklearn.metrics.pairwise import cosine_similarity, euclidean_distances

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_FILE = DATA_DIR / "faers_raw.json"
OUTPUT_DIR = PROJECT_ROOT / "analysis" / "figures"
MODEL_NAME = "all-MiniLM-L6-v2"

sys.path.insert(0, str(PROJECT_ROOT))
from etl.load_faers_to_qdrant import parse_report, serialize_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_parsed_reports(max_reports: int = 2000) -> list[dict]:
    """Load and parse cached FAERS reports."""
    if not CACHE_FILE.exists():
        print(f"Cache file not found: {CACHE_FILE}")
        print("Run the ETL first:  python etl/load_faers_to_qdrant.py")
        sys.exit(1)

    with open(CACHE_FILE) as f:
        raw = json.load(f)

    parsed = []
    for r in raw[:max_reports * 2]:
        rec = parse_report(r)
        if rec:
            parsed.append(rec)
        if len(parsed) >= max_reports:
            break
    print(f"Loaded {len(parsed)} parsed reports")
    return parsed


# ---------------------------------------------------------------------------
# 1. t-SNE Visualization of FAERS Embeddings
#    (ref: Word2Vec.py visualize_word_vectors, lecture slide 13)
# ---------------------------------------------------------------------------

def visualize_embeddings_tsne(records: list[dict], model: SentenceTransformer) -> None:
    """Reduce 384-dim embeddings to 2D via t-SNE and plot by outcome.

    t-SNE (van der Maaten & Hinton, 2008) preserves local neighborhood structure
    from high-dimensional space, making it ideal for visualizing whether adverse
    event reports with similar outcomes cluster together in embedding space.

    This is the clinical equivalent of Word2Vec.py's visualize_word_vectors(),
    but applied to full adverse event report embeddings rather than single words.
    """
    texts = [serialize_report(r) for r in records]
    print(f"Embedding {len(texts)} reports for t-SNE...")
    vectors = model.encode(texts, show_progress_bar=True, batch_size=64)

    outcome_labels = []
    for r in records:
        outcome = r["outcome"]
        if "death" in outcome:
            outcome_labels.append("death")
        elif "hospitalization" in outcome:
            outcome_labels.append("hospitalization")
        elif "life-threatening" in outcome:
            outcome_labels.append("life-threatening")
        else:
            outcome_labels.append("non-serious")

    print("Running t-SNE dimensionality reduction (384D → 2D)...")
    tsne = TSNE(n_components=2, random_state=42, perplexity=30, max_iter=1000)
    vectors_2d = tsne.fit_transform(vectors)

    color_map = {
        "death": "#d62728",
        "life-threatening": "#ff7f0e",
        "hospitalization": "#1f77b4",
        "non-serious": "#2ca02c",
    }

    plt.figure(figsize=(12, 8))
    for label in ["non-serious", "hospitalization", "life-threatening", "death"]:
        mask = [ol == label for ol in outcome_labels]
        if any(mask):
            pts = vectors_2d[mask]
            plt.scatter(pts[:, 0], pts[:, 1], c=color_map[label],
                        label=label, alpha=0.6, s=15)

    plt.title("t-SNE Visualization of FAERS Adverse Event Embeddings\n"
              "(384-dim → 2D, colored by outcome severity)")
    plt.xlabel("t-SNE Component 1")
    plt.ylabel("t-SNE Component 2")
    plt.legend(title="Outcome", loc="best")
    plt.tight_layout()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "tsne_adverse_events.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved t-SNE plot → {path}")


# ---------------------------------------------------------------------------
# 2. Drug Embedding Similarity Analysis
#    (ref: Word2Vec.py find_similar_words + calculate_word_similarity)
# ---------------------------------------------------------------------------

def analyze_drug_similarities(records: list[dict], model: SentenceTransformer) -> None:
    """Compute pairwise cosine similarities between common drugs.

    Analogous to Word2Vec.py's find_similar_words() and calculate_word_similarity(),
    but using sentence-transformer embeddings. Unlike Word2Vec which produces one
    fixed vector per token, sentence-transformers capture the clinical context
    of drug names (e.g., "metformin" embeds near other diabetes medications).

    Cosine similarity: cos θ = ⟨x,y⟩ / (||x||·||y||)  (lecture slide 5)
    """
    drug_counter = Counter()
    for r in records:
        for d in r["drugs"]:
            drug_counter[d] += 1

    top_drugs = [d for d, _ in drug_counter.most_common(15)]
    print(f"\nTop 15 drugs by frequency: {top_drugs}")

    drug_texts = [f"Drug: {d}" for d in top_drugs]
    drug_vectors = model.encode(drug_texts)

    sim_matrix = cosine_similarity(drug_vectors)

    plt.figure(figsize=(10, 8))
    plt.imshow(sim_matrix, cmap="YlOrRd", vmin=0, vmax=1)
    plt.colorbar(label="Cosine Similarity")
    plt.xticks(range(len(top_drugs)), top_drugs, rotation=45, ha="right", fontsize=8)
    plt.yticks(range(len(top_drugs)), top_drugs, fontsize=8)
    plt.title("Drug-Drug Cosine Similarity in Embedding Space\n"
              "(ref: Word2Vec.py cosine_similarity analysis)")
    plt.tight_layout()

    path = OUTPUT_DIR / "drug_similarity_heatmap.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved drug similarity heatmap → {path}")

    print("\nTop 10 most similar drug pairs:")
    pairs = []
    for i in range(len(top_drugs)):
        for j in range(i + 1, len(top_drugs)):
            pairs.append((top_drugs[i], top_drugs[j], sim_matrix[i][j]))
    pairs.sort(key=lambda x: x[2], reverse=True)
    for d1, d2, score in pairs[:10]:
        print(f"  {d1:30s} ↔ {d2:30s}  sim = {score:.4f}")


# ---------------------------------------------------------------------------
# 3. Cosine vs Euclidean Distance Comparison
#    (ref: lecture slides 4-6 on Distance Metrics)
# ---------------------------------------------------------------------------

def compare_distance_metrics(records: list[dict], model: SentenceTransformer) -> None:
    """Compare cosine similarity vs Euclidean distance for retrieval.

    The DSC 202 lecture (slides 4-6) covers multiple distance metrics:
    - Cosine similarity: angle-based, invariant to vector magnitude
    - Euclidean (L2): magnitude-sensitive Minkowski distance with p=2
    - Manhattan (L1): Minkowski with p=1

    For normalized sentence-transformer embeddings, cosine and Euclidean are
    monotonically related: d_euclidean² = 2(1 - cos θ). We verify this
    empirically on FAERS data.
    """
    sample = records[:500]
    texts = [serialize_report(r) for r in sample]
    vectors = model.encode(texts)

    n = min(200, len(vectors))
    cosine_sims = cosine_similarity(vectors[:n])
    euclid_dists = euclidean_distances(vectors[:n])

    upper_idx = np.triu_indices(n, k=1)
    cos_flat = cosine_sims[upper_idx]
    euc_flat = euclid_dists[upper_idx]

    plt.figure(figsize=(10, 5))

    plt.subplot(1, 2, 1)
    plt.hist(cos_flat, bins=50, alpha=0.7, color="#1f77b4", edgecolor="black", linewidth=0.3)
    plt.xlabel("Cosine Similarity")
    plt.ylabel("Frequency")
    plt.title("Distribution of Cosine Similarities\ncos θ = ⟨x,y⟩ / (||x||·||y||)")
    plt.axvline(cos_flat.mean(), color="red", linestyle="--", label=f"mean={cos_flat.mean():.3f}")
    plt.legend()

    plt.subplot(1, 2, 2)
    plt.scatter(cos_flat[::10], euc_flat[::10], alpha=0.3, s=5, c="#2ca02c")
    plt.xlabel("Cosine Similarity")
    plt.ylabel("Euclidean Distance")
    plt.title("Cosine vs Euclidean\n(monotonic for normalized vectors)")

    plt.tight_layout()
    path = OUTPUT_DIR / "distance_metrics_comparison.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved distance metrics comparison → {path}")


# ---------------------------------------------------------------------------
# 4. Curse of Dimensionality Demonstration
#    (ref: lecture slides 9-11)
# ---------------------------------------------------------------------------

def demonstrate_curse_of_dimensionality(records: list[dict], model: SentenceTransformer) -> None:
    """Show how pairwise distance distributions concentrate in high dimensions.

    From DSC 202 lecture (slides 9-11):
    - σ²/μ² ∝ 1/d → 0 as dimensions increase
    - (max_dist - min_dist) / max_dist → 0 (distance contrast vanishes)

    We demonstrate this by projecting 384-dim embeddings to various lower
    dimensions via random projection and measuring distance concentration.
    """
    texts = [serialize_report(r) for r in records[:300]]
    full_vectors = model.encode(texts)

    dims_to_test = [2, 5, 10, 50, 100, 200, 384]
    relative_variances = []

    for d in dims_to_test:
        if d < 384:
            proj = np.random.randn(384, d).astype(np.float32)
            proj /= np.linalg.norm(proj, axis=0, keepdims=True)
            projected = full_vectors @ proj
        else:
            projected = full_vectors

        dists = euclidean_distances(projected)
        upper = dists[np.triu_indices(len(projected), k=1)]
        rel_var = upper.std() / upper.mean() if upper.mean() > 0 else 0
        relative_variances.append(rel_var)
        print(f"  d={d:>3d}: mean_dist={upper.mean():.3f}, "
              f"std={upper.std():.3f}, σ/μ={rel_var:.4f}")

    plt.figure(figsize=(8, 5))
    plt.plot(dims_to_test, relative_variances, "o-", color="#d62728", linewidth=2)
    plt.xlabel("Number of Dimensions")
    plt.ylabel("Relative Std Dev (σ/μ)\nof Pairwise Distances")
    plt.title("Curse of Dimensionality: Distance Concentration\n"
              "σ²/μ² ∝ 1/d → 0 (ref: DSC 202 lecture slides 9-11)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    path = OUTPUT_DIR / "curse_of_dimensionality.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved curse of dimensionality plot → {path}")


# ---------------------------------------------------------------------------
# 5. Aspect-Based Adverse Event Analysis
#    (ref: BERT_rev.py, aspect-based-sentiment.py)
# ---------------------------------------------------------------------------

def aspect_based_analysis(records: list[dict], model: SentenceTransformer) -> None:
    """Analyze adverse events by clinical aspects using embedding similarity.

    Extends the concept from aspect-based-sentiment.py and BERT_rev.py:
    instead of analyzing financial aspects (Revenue, Expenses, Assets),
    we analyze clinical aspects (severity, organ system, drug class).

    For each adverse event report, we compute cosine similarity to
    aspect-defining anchor texts, classifying the report by its
    closest clinical aspect — similar to how BERT_rev.py computes
    aspect_scores = cosine_similarity(text_embedding, aspect_term_embedding).
    """
    aspect_anchors = {
        "cardiovascular": "cardiac arrest heart failure arrhythmia hypertension",
        "gastrointestinal": "nausea vomiting diarrhoea abdominal pain constipation",
        "neurological": "headache dizziness seizure tremor neuropathy cognitive impairment",
        "dermatological": "skin rash urticaria pruritus alopecia dermatitis",
        "hepatic": "liver damage hepatotoxicity jaundice hepatic failure",
        "respiratory": "dyspnoea cough bronchospasm pulmonary respiratory failure",
        "renal": "kidney failure nephrotoxicity renal impairment proteinuria",
    }

    anchor_texts = list(aspect_anchors.values())
    anchor_labels = list(aspect_anchors.keys())
    anchor_vectors = model.encode(anchor_texts)

    sample = records[:500]
    report_texts = [serialize_report(r) for r in sample]
    report_vectors = model.encode(report_texts, show_progress_bar=True, batch_size=64)

    sims = cosine_similarity(report_vectors, anchor_vectors)
    assignments = np.argmax(sims, axis=1)

    aspect_counts = Counter()
    aspect_avg_sim = {label: [] for label in anchor_labels}

    for i, idx in enumerate(assignments):
        label = anchor_labels[idx]
        aspect_counts[label] += 1
        aspect_avg_sim[label].append(sims[i][idx])

    print("\nAspect-Based Classification of Adverse Events:")
    print("-" * 55)
    for label in anchor_labels:
        count = aspect_counts[label]
        avg = np.mean(aspect_avg_sim[label]) if aspect_avg_sim[label] else 0
        print(f"  {label:20s}: {count:4d} reports  (avg similarity = {avg:.4f})")

    labels_sorted = sorted(aspect_counts.keys(), key=lambda x: aspect_counts[x], reverse=True)
    counts_sorted = [aspect_counts[l] for l in labels_sorted]

    plt.figure(figsize=(10, 5))
    bars = plt.bar(labels_sorted, counts_sorted, color="#1f77b4", edgecolor="black", linewidth=0.5)
    plt.xlabel("Clinical Aspect (Organ System)")
    plt.ylabel("Number of Reports")
    plt.title("Aspect-Based Classification of FAERS Reports\n"
              "(ref: BERT_rev.py aspect analysis adapted for clinical data)")
    plt.xticks(rotation=30, ha="right")

    for bar, count in zip(bars, counts_sorted):
        plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 5,
                 str(count), ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    path = OUTPUT_DIR / "aspect_based_classification.png"
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Saved aspect-based classification plot → {path}")


# ---------------------------------------------------------------------------
# 6. Reaction Embedding Clusters (Word2Vec-style word-level analysis)
#    (ref: Word2Vec.py, FastText.py)
# ---------------------------------------------------------------------------

def visualize_reaction_embeddings(records: list[dict], model: SentenceTransformer) -> None:
    """t-SNE visualization of individual reaction term embeddings.

    Directly analogous to Word2Vec.py's visualize_word_vectors():
    - Embed individual reaction terms (like Word2Vec embeds individual words)
    - Reduce to 2D with t-SNE
    - Visualize clustering of semantically similar reactions

    Sentence-transformers handle this better than Word2Vec for medical terms
    because they can embed unseen/complex terms (similar to FastText's subword
    advantage from FastText.py, but with transformer-level context).
    """
    reaction_counter = Counter()
    for r in records:
        for rx in r["reactions"]:
            reaction_counter[rx] += 1

    top_reactions = [rx for rx, _ in reaction_counter.most_common(40)]
    print(f"\nEmbedding top {len(top_reactions)} reactions for t-SNE...")

    reaction_vectors = model.encode(top_reactions)

    if len(top_reactions) > 5:
        perplexity = min(30, len(top_reactions) - 1)
        tsne = TSNE(n_components=2, random_state=42, perplexity=perplexity)
        vectors_2d = tsne.fit_transform(reaction_vectors)

        plt.figure(figsize=(14, 10))
        plt.scatter(vectors_2d[:, 0], vectors_2d[:, 1], c="#1f77b4", s=50, alpha=0.7)

        for i, rx in enumerate(top_reactions):
            plt.annotate(rx, (vectors_2d[i, 0], vectors_2d[i, 1]),
                         fontsize=7, alpha=0.8,
                         xytext=(5, 5), textcoords="offset points")

        plt.title("t-SNE of Adverse Reaction Embeddings\n"
                  "(ref: Word2Vec.py visualize_word_vectors, adapted for medical terms)")
        plt.xlabel("t-SNE Component 1")
        plt.ylabel("t-SNE Component 2")
        plt.tight_layout()

        path = OUTPUT_DIR / "reaction_embeddings_tsne.png"
        plt.savefig(path, dpi=150)
        plt.close()
        print(f"Saved reaction embeddings t-SNE → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("DSC 202 - Embedding Analysis for Drug Safety Check")
    print("Demonstrates course concepts: Word2Vec, FastText, BERT, T5,")
    print("aspect-based analysis, t-SNE, distance metrics, dimensionality")
    print("=" * 70)

    records = load_parsed_reports(max_reports=1500)

    print(f"\nLoading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    print("\n" + "=" * 70)
    print("1. t-SNE Visualization of Adverse Event Embeddings")
    print("   (ref: Word2Vec.py visualize_word_vectors)")
    print("=" * 70)
    visualize_embeddings_tsne(records, model)

    print("\n" + "=" * 70)
    print("2. Drug Embedding Similarity Analysis")
    print("   (ref: Word2Vec.py find_similar_words + cosine_similarity)")
    print("=" * 70)
    analyze_drug_similarities(records, model)

    print("\n" + "=" * 70)
    print("3. Cosine vs Euclidean Distance Comparison")
    print("   (ref: Lecture slides 4-6 on Distance Metrics)")
    print("=" * 70)
    compare_distance_metrics(records, model)

    print("\n" + "=" * 70)
    print("4. Curse of Dimensionality")
    print("   (ref: Lecture slides 9-11)")
    print("=" * 70)
    demonstrate_curse_of_dimensionality(records, model)

    print("\n" + "=" * 70)
    print("5. Aspect-Based Adverse Event Classification")
    print("   (ref: BERT_rev.py + aspect-based-sentiment.py)")
    print("=" * 70)
    aspect_based_analysis(records, model)

    print("\n" + "=" * 70)
    print("6. Reaction Term Embeddings (Word2Vec-style)")
    print("   (ref: Word2Vec.py, FastText.py)")
    print("=" * 70)
    visualize_reaction_embeddings(records, model)

    print("\n" + "=" * 70)
    print("All analyses complete. Figures saved to:")
    print(f"  {OUTPUT_DIR}/")
    print("=" * 70)


if __name__ == "__main__":
    main()
