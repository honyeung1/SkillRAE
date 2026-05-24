import os
import shutil
import hashlib
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEST_DIR = ROOT_DIR / "global_skill_pool"

def main():
    if DEST_DIR.exists():
        shutil.rmtree(DEST_DIR)
    DEST_DIR.mkdir()

    total_scanned = 0
    duplicates_count = 0
    collected_skills = []
    
    # Map of hash -> True (to detect exact duplicates)
    seen_hashes = set()
    # Map of skill_name -> True (to detect name collisions for different content)
    seen_names = set()

    print("Scanning for skills...")
    
    for root, dirs, files in os.walk(ROOT_DIR):
        # Skip the destination directory itself to avoid recursion if run multiple times
        if "global_skill_pool" in root:
            continue
            
        if "SKILL.md" in files:
            total_scanned += 1
            skill_md_path = Path(root) / "SKILL.md"
            skill_dir = Path(root)
            skill_name = skill_dir.name
            
            # Try to infer task name
            parts = skill_dir.parts
            task_name = "unknown_task"
            
            # Simple heuristic to find task name
            # tasks/<task_name>/...
            if "tasks" in parts:
                try:
                    idx = parts.index("tasks")
                    if idx + 1 < len(parts):
                        task_name = parts[idx+1]
                except ValueError:
                    pass
            elif "tasks-no-skills" in parts:
                try:
                    idx = parts.index("tasks-no-skills")
                    if idx + 1 < len(parts):
                        task_name = parts[idx+1]
                except ValueError:
                    pass
            
            # Calculate hash
            try:
                with open(skill_md_path, 'rb') as f:
                    content = f.read()
                    content_hash = hashlib.md5(content).hexdigest()
                    file_size = len(content)
            except Exception as e:
                print(f"Error reading {skill_md_path}: {e}")
                continue

            # Check duplicates
            if content_hash in seen_hashes:
                duplicates_count += 1
                continue
            
            seen_hashes.add(content_hash)
            
            # Determine final name
            final_name = skill_name
            if skill_name in seen_names:
                # Name collision with different content
                final_name = f"{task_name}_{skill_name}"
                print(f"Name collision for {skill_name}. Renaming to {final_name}")
                
                # Handle double collision (e.g. if taskname_skillname also exists?)
                # Unlikely but good practice
                if (DEST_DIR / final_name).exists():
                     final_name = f"{final_name}_{content_hash[:6]}"
            
            seen_names.add(skill_name) # Record original name as seen
            
            # Copy directory
            dest_path = DEST_DIR / final_name
            try:
                shutil.copytree(skill_dir, dest_path)
            except Exception as e:
                print(f"Error copying {skill_dir} to {dest_path}: {e}")
                continue
                
            # Record metadata
            collected_skills.append({
                "skill_name": final_name, # Using final name in pool
                "original_name": skill_name,
                "original_task": task_name,
                "original_path": str(skill_dir),
                "hash": content_hash,
                "file_size": file_size
            })

    # Write index
    index_path = DEST_DIR / "skill_index.json"
    with open(index_path, "w") as f:
        json.dump(collected_skills, f, indent=2)

    print("-" * 30)
    print(f"Total skills scanned: {total_scanned}")
    print(f"Duplicates detected (skipped): {duplicates_count}")
    print(f"Total unique skills collected: {len(collected_skills)}")
    print("-" * 30)
    
    # Print Directory Tree
    print("\nDirectory Tree of /global_skill_pool/:")
    # Simple tree printer
    sorted_items = sorted(os.listdir(DEST_DIR))
    for item in sorted_items:
        print(f"├── {item}")

if __name__ == "__main__":
    main()
