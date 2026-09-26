import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

CONFIG_FILE = Path(__file__).with_name("config.json")
WORK_ITEM_QUERY_PAGE_SIZE = 1000
FIELD_NAMES = [
    "System.Id",
    "System.WorkItemType",
    "System.State",
    "System.Title",
    "System.AssignedTo",
    "System.IterationPath",
    "System.AreaPath",
    "System.Description",
    "Microsoft.VSTS.CMMI.Comments",
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


def fetch_discussion(az_command, org_url, project, work_item_id):
    discussion = []
    continuation_token = None

    while True:
        command = [
            az_command, "devops", "invoke",
            "--org", org_url,
            "--area", "wit",
            "--resource", "updates",
            "--route-parameters", f"project={project}", f"id={work_item_id}",
            "--api-version", "7.1",
            "-o", "json",
        ]

        if continuation_token:
            command.extend(["--query-parameters", f"continuationToken={continuation_token}"])

        response = run_az(command)
        for update in response.get("value", []):
            fields = update.get("fields", {})
            history = fields.get("System.History", {})
            discussion_text = history.get("newValue")

            if not discussion_text:
                continue

            changed_date = fields.get("System.ChangedDate", {}).get("newValue")
            revised_by = update.get("revisedBy", {})

            discussion.append({
                "addedBy": revised_by.get("displayName"),
                "addedDate": changed_date or update.get("revisedDate"),
                "discussion": discussion_text
            })

        continuation_token = response.get("continuation_token")

        if not continuation_token:
            break

    return discussion


def get_assigned_to(fields):
    assigned_to = fields.get("System.AssignedTo")

    if isinstance(assigned_to, dict):
        return assigned_to.get("displayName")

    return assigned_to


def title_matches_filter(work_item, title_filter):
    if not title_filter:
        return True

    title = work_item.get("fields", {}).get("System.Title", "")
    if not title:
        return False

    pattern = re.compile(title_filter, re.IGNORECASE)
    return bool(pattern.search(title))


def fetch_work_item_ids(az_command, org_url, project, work_item_type):
    work_item_ids = []
    last_id = 0

    while True:
        wiql = (
            "SELECT [System.Id] "
            "FROM WorkItems "
            f"WHERE [System.TeamProject] = '{project}' "
            f"AND [System.WorkItemType] = '{work_item_type}' "
            f"AND [System.Id] > {last_id} "
            "ORDER BY [System.Id]"
        )

        items = run_az(
            [
                az_command, "boards", "query",
                "--org", org_url,
                "--project", project,
                "--wiql", wiql,
                "-o", "json"
            ]
        )

        if not items:
            break

        page_ids = [item["id"] for item in items]
        work_item_ids.extend(page_ids)

        if len(page_ids) < WORK_ITEM_QUERY_PAGE_SIZE:
            break

        next_last_id = page_ids[-1]
        if next_last_id <= last_id:
            raise RuntimeError("Azure DevOps returned a non-advancing work item page.")

        last_id = next_last_id

    return work_item_ids


def build_parser():
    parser = argparse.ArgumentParser(description="Export Azure DevOps work items to JSON.")
    parser.add_argument(
        "--title-filter",
        dest="title_filter",
        help="Filter titles using a case-insensitive regular expression.",
    )
    return parser


def build_work_item_link(org_url, project, work_item):
    work_item_id = work_item.get("id")
    project_path = quote(project, safe="")
    return f"{org_url}/{project_path}/_workitems/edit/{work_item_id}/"


def build_output_item(org_url, project, work_item):
    fields = work_item.get("fields", {})
    return {
        "id": work_item.get("id"),
        "link-json": work_item.get("url"),
        "link": build_work_item_link(org_url, project, work_item),
        "type": fields.get("System.WorkItemType"),
        "state": fields.get("System.State"),
        "title": fields.get("System.Title"),
        "assignedTo": get_assigned_to(fields),
        "iterationPath": fields.get("System.IterationPath"),
        "areaPath": fields.get("System.AreaPath"),
        "description": fields.get("System.Description"),
        "comments": fields.get("Microsoft.VSTS.CMMI.Comments"),
        "acceptanceCriteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
    }


def build_markdown_output(items):
    lines = ["# Azure DevOps Work Items", ""]

    for item in items:
        title = item.get("title") or "Untitled"
        lines.extend([
            f"## {item.get('id')}: {title}",
            "",
            f"- **Type:** {item.get('type') or ''}",
            f"- **State:** {item.get('state') or ''}",
            f"- **Assigned to:** {item.get('assignedTo') or ''}",
            f"- **Iteration path:** {item.get('iterationPath') or ''}",
            f"- **Area path:** {item.get('areaPath') or ''}",
            f"- **Link:** [{item.get('link')}]({item.get('link')})",
            "",
        ])

        for heading, field in (
            ("Description", "description"),
            ("Acceptance criteria", "acceptanceCriteria"),
            ("Comments", "comments"),
        ):
            value = item.get(field)
            if value:
                lines.extend([f"### {heading}", "", str(value), ""])

        discussions = item.get("discussion", [])
        if discussions:
            lines.extend(["### Discussion", ""])
            for discussion in discussions:
                author = discussion.get("addedBy") or "Unknown"
                date = discussion.get("addedDate") or ""
                text = discussion.get("discussion") or ""
                lines.extend([
                    f"#### {author} ({date})",
                    "",
                    text,
                    "",
                ])

    return "\n".join(lines)


def main():
    args = build_parser().parse_args()
    config = load_config()
    organization = config["organization"]
    project = config["project"]
    work_item_type = config["work_item_type"]
    title_filter = args.title_filter or config.get("title_filter", "")
    include_discussions = config.get("include_discussions", False)
    output_filename = Path(config["output_file"]).name
    output_file = Path(__file__).with_name("out") / output_filename
    output_file.parent.mkdir(parents=True, exist_ok=True)
    org_url = f"https://dev.azure.com/{organization}"
    az_command = get_az_command()

    print(f"Getting work items of type {work_item_type}...")

    work_item_ids = fetch_work_item_ids(
        az_command,
        org_url,
        project,
        work_item_type
    )
    print(f"Exporting {len(work_item_ids)} work items...")

    work_items = fetch_work_items(az_command, org_url, project, work_item_ids)
    work_items = [
        work_item
        for work_item in work_items
        if title_matches_filter(work_item, title_filter)
    ]

    if title_filter:
        print(f"Matched {len(work_items)} work items with title filter {title_filter!r}.")

    output = []
    for work_item in work_items:
        output_item = build_output_item(org_url, project, work_item)

        if include_discussions:
            print(f"Exporting discussion for work item {work_item['id']}...")
            output_item["discussion"] = fetch_discussion(
                az_command,
                org_url,
                project,
                work_item["id"]
            )

        output.append(output_item)

    output_file.write_text(build_markdown_output(output), encoding="utf-8")

    print(f"Done: {output_file}")


if __name__ == "__main__":
    main()
