import json
import shutil
import subprocess
import tempfile
from pathlib import Path

CONFIG_FILE = Path(__file__).with_name("config.json")
FIELD_NAMES = [
    "System.Id",
    "System.WorkItemType",
    "System.State",
    "System.Title",
    "System.AssignedTo",
    "System.IterationPath",
    "System.AreaPath",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
]


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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return json.loads(result.stdout)


def get_az_command():
    command = shutil.which("az.cmd") or shutil.which("az")

    if not command:
        raise RuntimeError("Azure CLI was not found in PATH.")

    return command


def chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def fetch_work_items(az_command, org_url, project, work_item_ids):
    work_items = []

    for work_item_id_chunk in chunked(work_item_ids, 200):
        request = {
            "ids": work_item_id_chunk,
            "fields": FIELD_NAMES,
            "errorPolicy": "Omit",
        }

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False
        ) as file:
            json.dump(request, file)
            request_file = Path(file.name)

        try:
            response = run_az(
                [
                    az_command, "devops", "invoke",
                    "--org", org_url,
                    "--area", "wit",
                    "--resource", "workitemsbatch",
                    "--route-parameters", f"project={project}",
                    "--http-method", "POST",
                    "--api-version", "7.1",
                    "--in-file", str(request_file),
                    "-o", "json",
                ]
            )
        finally:
            request_file.unlink(missing_ok=True)

        if isinstance(response, dict):
            work_items.extend(response.get("value", []))
        else:
            work_items.extend(response)

    return work_items


def get_assigned_to(fields):
    assigned_to = fields.get("System.AssignedTo")

    if isinstance(assigned_to, dict):
        return assigned_to.get("displayName")

    return assigned_to


def main():
    config = load_config()
    organization = config["organization"]
    project = config["project"]
    work_item_type = config["work_item_type"]
    output_file = Path(config["output_file"])
    org_url = f"https://dev.azure.com/{organization}"
    az_command = get_az_command()

    wiql = (
        "SELECT [System.Id] "
        "FROM WorkItems "
        f"WHERE [System.TeamProject] = '{project}' "
        f"AND [System.WorkItemType] = '{work_item_type}' "
        "ORDER BY [System.Id]"
    )

    print(f"Getting work items of type {work_item_type}...")

    items = run_az(
        [
            az_command, "boards", "query",
            "--org", org_url,
            "--project", project,
            "--wiql", wiql,
            "-o", "json"
        ]
    )

    work_item_ids = [item["id"] for item in items]
    print(f"Exporting {len(work_item_ids)} work items...")

    work_items = fetch_work_items(az_command, org_url, project, work_item_ids)

    output = []
    for work_item in work_items:
        fields = work_item.get("fields", {})
        output.append({
            "id": work_item.get("id"),
            "type": fields.get("System.WorkItemType"),
            "state": fields.get("System.State"),
            "title": fields.get("System.Title"),
            "assignedTo": get_assigned_to(fields),
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
