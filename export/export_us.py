import json
import subprocess
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("config.json")


def load_config(config_file=CONFIG_FILE):
    with config_file.open(encoding="utf-8") as file:
        config = json.load(file)

    required_fields = ["organization", "project", "work_item_type", "output_file"]
    missing_fields = [field for field in required_fields if not config.get(field)]

    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ValueError(f"Missing required config field(s): {missing}")

    return config


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


def main():
    config = load_config()
    organization = config["organization"]
    project = config["project"]
    work_item_type = config["work_item_type"]
    output_file = Path(config["output_file"])
    org_url = f"https://dev.azure.com/{organization}"

    wiql = f"""
    SELECT [System.Id]
    FROM WorkItems
    WHERE [System.TeamProject] = '{project}'
    AND [System.WorkItemType] = '{work_item_type}'
    ORDER BY [System.Id]
    """

    print(f"Getting {work_item_type}s...")

    items = run_az(
        f'az boards query '
        f'--org "{org_url}" '
        f'--project "{project}" '
        f'--wiql "{wiql}" '
        f'-o json'
    )

    output = []

    for item in items:
        work_item_id = item["id"]
        print(f"Exporting work item {work_item_id}...")

        wi = run_az(
            f'az boards work-item show '
            f'--id {work_item_id} '
            f'--org "{org_url}" '
            f'--project "{project}" '
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

    output_file.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Done: {output_file}")


if __name__ == "__main__":
    main()
