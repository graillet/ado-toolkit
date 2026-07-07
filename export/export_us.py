import json
import subprocess
from pathlib import Path

ORG = "NABusinessTechnology"
PROJECT = "Ivy XPress Acceleration"
OUTPUT_FILE = Path("ado_user_stories.json")

ORG_URL = f"https://dev.azure.com/{ORG}"


def run_az(command):
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return json.loads(result.stdout)


wiql = f"""
SELECT [System.Id]
FROM WorkItems
WHERE [System.TeamProject] = '{PROJECT}'
AND [System.WorkItemType] = 'User Story'
ORDER BY [System.Id]
"""

print("Getting User Stories...")

items = run_az(
    f'az boards query '
    f'--org "{ORG_URL}" '
    f'--project "{PROJECT}" '
    f'--wiql "{wiql}" '
    f'-o json'
)

output = []

for item in items:
    work_item_id = item["id"]
    print(f"Exporting US {work_item_id}...")

    wi = run_az(
        f'az boards work-item show '
        f'--id {work_item_id} '
        f'--org "{ORG_URL}" '
        f'--project "{PROJECT}" '
        f'-o json'
    )

    fields = wi.get("fields", {})

    output.append({
        "id": wi.get("id"),
        "type": fields.get("System.WorkItemType"),
        "state": fields.get("System.State"),
        "title": fields.get("System.Title"),
        "assignedTo": fields.get("System.AssignedTo", {}).get("displayName"),
        "iterationPath": fields.get("System.IterationPath"),
        "areaPath": fields.get("System.AreaPath"),
        "description": fields.get("System.Description"),
        "acceptanceCriteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
    })

OUTPUT_FILE.write_text(
    json.dumps(output, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

print(f"Done: {OUTPUT_FILE}")