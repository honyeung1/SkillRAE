import json
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

# Configuration
DATA_DIR = Path(__file__).resolve().parents[1]
MODEL_NAME = "BAAI/bge-small-en"

def main():
    print(f"Loading data from {DATA_DIR}...")
    
    subunit_path = DATA_DIR / "subunit_nodes.json"
    if not subunit_path.exists():
        print(f"Error: {subunit_path} not found.")
        return

    with open(subunit_path, "r") as f:
        subunits = json.load(f)
    
    subunit_ids = [s["id"] for s in subunits]
    subunit_texts = [s["text"] for s in subunits]
    
    count = len(subunits)
    print(f"Found {count} subunits.")
    
    print(f"Loading embedding model: {MODEL_NAME}...")
    # Using 'cpu' device explicitly just to be safe, though default is usually fine
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    
    print("Generating embeddings...")
    embeddings = model.encode(
        subunit_texts,
        batch_size=32,
        show_progress_bar=True,
        normalize_embeddings=True
    )
    
    print("Saving outputs...")
    # Save IDs
    with open(DATA_DIR / "subunit_ids.json", "w") as f:
        json.dump(subunit_ids, f)
        
    # Save Embeddings
    np.save(DATA_DIR / "subunit_embeddings.npy", embeddings)
    
    print("-" * 40)
    print(f"Number of subunits embedded: {count}")
    print(f"Embedding dimension:       {embeddings.shape[1]}")
    print(f"Shape of saved array:      {embeddings.shape}")
    print("-" * 40)

if __name__ == "__main__":
    main()
