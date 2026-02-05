import json, os, subprocess, sys
from datetime import datetime, timezone

def sh(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {cmd}\nSTDERR:\n{p.stderr}")
    return p.stdout.strip()

def main():
    epic = os.getenv("BEADS_EPIC_ID", "").strip()

    # Beads provides JSON output for ready work.
    out_ready = sh("bd ready --json")
    ready = json.loads(out_ready) if out_ready else []
    if epic:
        ready = [x for x in ready if str(x.get("id","")).startswith(epic)]
    if not ready:
        print("No ready Beads issues found (or none match BEADS_EPIC_ID).")
        sys.exit(2)

    chosen = ready[0]
    beads_id = chosen.get("id") or chosen.get("ID") or chosen.get("issue_id")
    if not beads_id:
        raise RuntimeError(f"Unexpected bd ready --json shape: {chosen}")

    details = json.loads(sh(f"bd show {beads_id} --json"))
    title = details.get("title") or details.get("Title") or chosen.get("title") or str(beads_id)
    body = details.get("description") or details.get("body") or details.get("Desc") or ""

    snap = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "beads_epic_id": epic or None,
        "current": {"beads_id": beads_id, "title": title, "description": body},
        "tasks": [{
            "id": 1, "status": "pending", "beads_id": beads_id,
            "title": title, "description": body
        }]
    }

    with open("tasks.json", "w") as f:
        json.dump(snap, f, indent=2)

    os.makedirs(".ralph", exist_ok=True)
    with open(".ralph/current_task.json", "w") as f:
        json.dump(snap["current"], f, indent=2)

    print(beads_id)

if __name__ == "__main__":
    main()

