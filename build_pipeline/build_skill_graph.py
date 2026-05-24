import os
import re
import json
import string
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).resolve().parents[1]
POOL_DIR = REPO_ROOT / "global_skill_pool"
OUTPUT_DIR = REPO_ROOT

# Heuristics
CONSTRAINT_KEYWORDS = [
    "must", "should", "avoid", "cannot", "required", "only if", "limitation"
]

def normalize_text(text):
    """
    Normalize text: lowercase, strip punctuation, remove extra whitespace.
    """
    # Lowercase
    text = text.lower()
    # Strip punctuation
    text = text.translate(str.maketrans('', '', string.punctuation))
    # Remove extra whitespace
    text = " ".join(text.split())
    return text

def extract_elements_from_line(line):
    """
    Extract elements like libraries, filenames, CLI commands from a line.
    """
    elements = set()
    
    # 1. CLI Commands: Contain spaces + flags like --
    # Heuristic: looks like `cmd ... --flag`
    if "--" in line and re.search(r'\s--[a-zA-Z0-9-]+', line):
        elements.add(line.strip())

    # 2. Filenames: ending in specific extensions
    # Regex for tokens ending in .py, .json, etc.
    filenames = re.findall(r'\b[\w\-]+\.(?:py|json|yaml|yml|sh|md|txt)\b', line, re.IGNORECASE)
    elements.update(filenames)

    # 3. Library names
    # Heuristic: import X, use X, install X
    # Capture the word after these keywords
    lib_matches = re.findall(r'\b(?:import|use|using|install)\s+([a-zA-Z0-9_\-]+)', line, re.IGNORECASE)
    for lib in lib_matches:
        # Filter out common stop words that might follow "use"
        if lib.lower() not in {"the", "a", "an", "in", "on", "to", "for", "this", "that"}:
            elements.add(lib)
            
    return list(elements)

def parse_skill_file(file_path):
    """
    Parse a SKILL.md file to extract steps, elements, and constraints.
    """
    steps = []
    elements = []
    constraints = []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}")
        return {"steps": [], "elements": [], "constraints": []}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Skip headers
        if line.startswith("#"):
            continue

        # 1. Constraints
        # Check for keywords with word boundaries AND length limit
        if len(line) < 200:
            line_lower = line.lower()
            is_constraint = False
            for kw in CONSTRAINT_KEYWORDS:
                # Simple check first, then regex for boundary
                if kw in line_lower:
                    if re.search(rf'\b{re.escape(kw)}\b', line_lower):
                        constraints.append(line)
                        is_constraint = True
                        break
        
        # 2. Steps
        # A) Remove heuristic: r'^[A-Z][a-z]+'
        # Keep only: numbered steps (^\d+\.) and bullet points (- or *)
        is_step = False
        if re.match(r'^\d+\.', line): # "1. "
            is_step = True
        elif line.startswith("- ") or line.startswith("* "): # "- "
            is_step = True
        
        if is_step:
            steps.append(line)

        # 3. Elements
        # Extract from the line content
        line_elems = extract_elements_from_line(line)
        elements.extend(line_elems)

    return {
        "steps": steps,
        "elements": elements,
        "constraints": constraints
    }

def main():
    if not POOL_DIR.exists():
        print(f"Error: Directory {POOL_DIR} not found.")
        return

    # Data structures
    skill_nodes = [] # [{"id":..., "name":...}]
    subunit_nodes = [] # [{"id":..., "text":...}]
    edges = [] # [{"skill_id":..., "subunit_id":...}]

    # Registry for deduplication
    # normalized_text -> subunit_id
    subunit_registry = {}
    next_subunit_id = 1
    
    # Track edges to avoid duplicates: set of (skill_id, subunit_id)
    edge_set = set()

    # 1. Parse Skills
    skills_dirs = sorted([d for d in os.listdir(POOL_DIR) if (POOL_DIR / d).is_dir()])
    
    print(f"Parsing {len(skills_dirs)} skills from {POOL_DIR}...")
    
    for idx, skill_name in enumerate(skills_dirs):
        skill_path = POOL_DIR / skill_name / "SKILL.md"
        if not skill_path.exists():
            continue

        skill_id = f"skill_{idx+1:03d}"
        skill_nodes.append({
            "id": skill_id,
            "name": skill_name
        })

        # Parse file
        parsed_data = parse_skill_file(skill_path)
        
        # Combine all subunits
        all_subunits = (
            parsed_data["steps"] + 
            parsed_data["elements"] + 
            parsed_data["constraints"]
        )

        # 2. Normalize & Deduplicate
        for subunit_raw in all_subunits:
            norm_text = normalize_text(subunit_raw)
            
            if not norm_text:
                continue
            
            # B) Subunit filtering:
            # - token count < 3
            # - token count > 40
            tokens = norm_text.split()
            token_count = len(tokens)
            
            if token_count < 3 or token_count > 40:
                continue

            if norm_text not in subunit_registry:
                # Create new subunit
                sub_id = f"sub_{next_subunit_id:04d}"
                subunit_registry[norm_text] = sub_id
                next_subunit_id += 1
                
                subunit_nodes.append({
                    "id": sub_id,
                    "text": norm_text
                })
            
            sub_id = subunit_registry[norm_text]

            # 3. Build Edges
            if (skill_id, sub_id) not in edge_set:
                edge_set.add((skill_id, sub_id))
                edges.append({
                    "skill_id": skill_id,
                    "subunit_id": sub_id
                })

    # 4. Save Output
    print("Saving JSON files...")
    
    with open(OUTPUT_DIR / "skill_nodes.json", "w") as f:
        json.dump(skill_nodes, f, indent=2)
        
    with open(OUTPUT_DIR / "subunit_nodes.json", "w") as f:
        json.dump(subunit_nodes, f, indent=2)
        
    with open(OUTPUT_DIR / "edges.json", "w") as f:
        json.dump(edges, f, indent=2)

    # Statistics
    total_skills = len(skill_nodes)
    total_subunits = len(subunit_nodes)
    total_edges = len(edges)
    avg_subunits = total_edges / total_skills if total_skills > 0 else 0

    print("-" * 40)
    print(f"Total skills parsed:      {total_skills}")
    print(f"Total unique subunits:    {total_subunits}")
    print(f"Total edges created:      {total_edges}")
    print(f"Average subunits per skill: {avg_subunits:.2f}")
    print("-" * 40)

if __name__ == "__main__":
    main()
