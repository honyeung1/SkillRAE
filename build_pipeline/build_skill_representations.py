import json
import math
import collections
from pathlib import Path

# Configuration
DATA_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = DATA_DIR / "skill_representations.json"
TOP_K = 10

def main():
    print(f"Loading graph data from {DATA_DIR}...")
    try:
        with open(DATA_DIR / "skill_nodes.json", "r") as f:
            skill_nodes = json.load(f)
        
        with open(DATA_DIR / "subunit_nodes.json", "r") as f:
            subunit_nodes = json.load(f)
            
        with open(DATA_DIR / "edges.json", "r") as f:
            edges = json.load(f)
            
    except FileNotFoundError as e:
        print(f"Error loading files: {e}")
        return

    # Build Mappings
    sid_to_name = {s["id"]: s["name"] for s in skill_nodes}
    uid_to_text = {u["id"]: u["text"] for u in subunit_nodes}
    
    sid_to_uids = collections.defaultdict(set)
    uid_to_sids = collections.defaultdict(set)
    
    for e in edges:
        sid = e["skill_id"]
        uid = e["subunit_id"]
        sid_to_uids[sid].add(uid)
        uid_to_sids[uid].add(sid)
        
    total_skills = len(skill_nodes)
    print(f"Total skills: {total_skills}")
    
    # 1. Compute IDF for each subunit
    # idf(u) = log(total_skills / count(skills_containing_u))
    subunit_idf = {}
    for uid, sids in uid_to_sids.items():
        doc_freq = len(sids)
        if doc_freq > 0:
            subunit_idf[uid] = math.log(total_skills / doc_freq)
        else:
            subunit_idf[uid] = 0.0

    # 2. Build Representations
    representations = {}
    total_subunits_selected = 0
    
    for skill in skill_nodes:
        sid = skill["id"]
        name = skill["name"]
        
        # Get connected subunits
        uids = list(sid_to_uids.get(sid, []))
        
        # Filter very short subunits (< 3 tokens)
        valid_uids = []
        for uid in uids:
            text = uid_to_text.get(uid, "")
            if len(text.split()) >= 3:
                valid_uids.append(uid)
        
        # Rank by IDF descending
        # Higher IDF = more specific/unique to few skills
        ranked_uids = sorted(valid_uids, key=lambda u: subunit_idf.get(u, 0), reverse=True)
        
        # Select Top-K
        selected_uids = ranked_uids[:TOP_K]
        total_subunits_selected += len(selected_uids)
        
        # Build Keyword Bag String
        tokens = set()
        for uid in selected_uids:
            text = uid_to_text.get(uid, "")
            for token in text.split():
                token = token.strip().lower()
                # Keep only tokens > 2 chars
                if len(token) > 2:
                    tokens.add(token)
        
        # representation = skill_name + sorted unique keywords
        rep_string = f"{name} {' '.join(sorted(tokens))}"
        
        representations[sid] = rep_string.strip()

    # Save
    print(f"Saving {len(representations)} representations to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(representations, f, indent=2)
        
    # Stats
    avg_subunits = total_subunits_selected / total_skills if total_skills > 0 else 0
    print("-" * 30)
    print(f"Total skills processed:       {total_skills}")
    print(f"Average subunits per skill:   {avg_subunits:.2f}")
    print("-" * 30)

if __name__ == "__main__":
    main()
