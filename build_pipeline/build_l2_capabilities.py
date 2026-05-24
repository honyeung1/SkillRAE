'''
这段代码的核心作用是：把一批“技能（skills）”按照语义相似度自动聚类，并生成一个二级分类（L2 taxonomy）映射文件。
简单说：用文本嵌入 + KMeans，把很多 skill 自动分组，并给每组生成一个标签。
'''


import json
import math
import numpy as np
import collections
from pathlib import Path
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans

# Configuration
DATA_DIR = Path(__file__).resolve().parents[1]
INPUT_FILE = DATA_DIR / "skill_representations.json"
NODES_FILE = DATA_DIR / "skill_nodes.json"
OUTPUT_FILE = DATA_DIR / "skill_l2_mapping.json"
MODEL_NAME = "BAAI/bge-small-en"
RANDOM_STATE = 42

def get_cluster_label(top_skill_names):
    """
    Generate a deterministic label from top-5 centroid-nearest skill names.
    Strategy: Extract tokens, find most frequent meaningful words.
    """
    tokens = []
    for name in top_skill_names:
        # Split by non-alphanumeric chars (-, _, space)
        parts = name.replace("-", " ").replace("_", " ").split()
        for p in parts:
            p = p.lower()
            if len(p) > 3 and p not in {"skill", "with", "from", "using", "into", "task", "analysis", "processing"}:
                tokens.append(p)
                
    if not tokens:
        return "misc-capabilities"
        
    # Count frequency
    counts = collections.Counter(tokens)
    
    # Take top 2 most common tokens
    # If tie, deterministic due to Counter stability or sort
    top_tokens = [t for t, _ in counts.most_common(2)]
    
    return "-".join(top_tokens)

def main():
    print(f"Loading data from {DATA_DIR}...")
    try:
        # Load representations (for embeddings)
        with open(INPUT_FILE, "r") as f:
            representations = json.load(f)
            
        # Load skill names (for labeling)
        with open(NODES_FILE, "r") as f:
            nodes = json.load(f)
            sid_to_name = {n["id"]: n["name"] for n in nodes}
            
    except FileNotFoundError:
        print("Error: Input files not found.")
        return

    # Ensure consistent ordering
    skill_ids = sorted(list(representations.keys()))
    skill_texts = [representations[sid] for sid in skill_ids]
    skill_names = [sid_to_name.get(sid, sid) for sid in skill_ids]
    
    n_skills = len(skill_ids)
    n_clusters = int(math.sqrt(n_skills))
    print(f"Loaded {n_skills} skills. Targeting {n_clusters} clusters.")
    
    print(f"Loading embedding model: {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    
    print("Generating embeddings...")
    embeddings = model.encode(
        skill_texts,
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True
    )
    
    print(f"Clustering with KMeans (k={n_clusters})...")
    kmeans = KMeans(
        n_clusters=n_clusters,
        random_state=RANDOM_STATE,
        n_init=10
    )
    cluster_labels = kmeans.fit_predict(embeddings)
    cluster_centers = kmeans.cluster_centers_
    
    # Organize results
    clusters = collections.defaultdict(list)
    for i, label in enumerate(cluster_labels):
        clusters[int(label)].append(i) # Store index
        
    # Build Output Mapping
    l2_mapping = {}
    
    print("Generating cluster labels...")
    
    # Sort clusters by size descending for cleaner output ID assignment
    sorted_cluster_ids = sorted(clusters.keys(), key=lambda k: len(clusters[k]), reverse=True)
    
    for i, cluster_id in enumerate(sorted_cluster_ids):
        indices = clusters[cluster_id]
        
        # Get centroid
        centroid = cluster_centers[cluster_id]
        
        # Calculate similarity to centroid for all skills in this cluster
        # (N, D) @ (D,) -> (N,)
        cluster_embeddings = embeddings[indices]
        similarities = cluster_embeddings @ centroid
        
        # Sort indices by similarity descending
        sorted_indices_local = np.argsort(similarities)[::-1]
        sorted_indices_global = [indices[idx] for idx in sorted_indices_local]
        
        # Get top-5 nearest skills for labeling
        top_k_indices = sorted_indices_global[:5]
        top_skill_names = [skill_names[idx] for idx in top_k_indices]
        
        # Generate Label
        label = get_cluster_label(top_skill_names)
        
        # Get all skill IDs in this cluster (sorted by similarity to centroid)
        cluster_sids = [skill_ids[idx] for idx in sorted_indices_global]
        
        l2_id = f"l2_{i+1:03d}"
        l2_mapping[l2_id] = {
            "label": label,
            "skills": cluster_sids,
            "count": len(cluster_sids)
        }

    # Save
    print(f"Saving L2 mapping to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(l2_mapping, f, indent=2)
        
    # Report
    print("-" * 40)
    print(f"Number of clusters created: {len(l2_mapping)}")
    
    sizes = [c["count"] for c in l2_mapping.values()]
    print(f"Cluster sizes: Min={min(sizes)}, Max={max(sizes)}, Avg={sum(sizes)/len(sizes):.1f}")
    
    # Print example cluster (largest one)
    example_id = list(l2_mapping.keys())[0]
    ex = l2_mapping[example_id]
    print(f"\nExample Cluster ({example_id}): {ex['label']}")
    print(f"Skills ({ex['count']}):")
    # Show top 5 nearest to centroid
    for sid in ex["skills"][:5]:
        name = sid_to_name.get(sid, sid)
        print(f"  - {name}")
    print("-" * 40)

if __name__ == "__main__":
    main()
