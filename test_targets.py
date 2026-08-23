from utils import load_config

config = load_config()
targets = config.get("mpt", {}).get("targets", [])
active_targets = [t for t in targets if t.get("enabled")]
print(f"Targets: {[t['id'] for t in targets]}")
print(f"Active: {[t['id'] for t in active_targets]}")

target_id = "ps3"
filtered = [t for t in active_targets if t["id"] == target_id]
print(f"Filtered for {target_id}: {[t['id'] for t in filtered]}")
