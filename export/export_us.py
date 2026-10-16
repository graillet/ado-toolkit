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
WORK_ITEM_UPDATES_PAGE_SIZE = 200
SUMMARY_FIELD_NAMES = [
    "System.Id",
    "System.WorkItemType",
    "System.State",
    "System.Reason",
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
            batch_items = response.get("value", [])
        else:
            batch_items = response

        returned_ids = {item["id"] for item in batch_items}
        missing_ids = set(work_item_id_chunk) - returned_ids
        if missing_ids:
            raise RuntimeError(
                "Export incomplete: Azure DevOps omitted work item IDs: "
                + ", ".join(map(str, sorted(missing_ids)))
            )
        work_items.extend(batch_items)

    return work_items


def fetch_discussion(az_command, org_url, project, work_item_id):
    discussion = []
    skip = 0

    while True:
        command = [
            az_command, "devops", "invoke",
            "--org", org_url,
            "--area", "wit",
            "--resource", "updates",
            "--route-parameters", f"project={project}", f"id={work_item_id}",
            "--api-version", "7.1",
            "--query-parameters", f"$top={WORK_ITEM_UPDATES_PAGE_SIZE}", f"$skip={skip}",
            "-o", "json",
        ]

        response = run_az(command)
        updates = response["value"]
        if not updates:
            break
        for update in updates:
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

        skip += len(updates)

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


def parse_sprints(value, field_name):
    """Parse pipe-separated sprint names, ignoring case and surrounding spaces."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string of sprint names separated by '|'.")
    return {name.strip().casefold() for name in value.split("|") if name.strip()}


def parse_excluded_sprints(value):
    return parse_sprints(value, "exclude_sprints")


def sprint_matches(work_item, sprints):
    iteration_path = work_item.get("fields", {}).get("System.IterationPath") or ""
    sprint_name = iteration_path.rsplit("\\", 1)[-1]
    return sprint_name.casefold() in sprints


def sprint_is_excluded(work_item, excluded_sprints):
    return sprint_matches(work_item, excluded_sprints)


def validate_filters(filters):
    if not isinstance(filters, list):
        raise ValueError("filters must be a JSON array.")
    unary = {"exists", "not_exists", "is_empty"}
    operators = unary | {"eq", "ne", "in", "not_in", "contains"}
    for index, rule in enumerate(filters):
        prefix = f"filters[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{prefix} must be an object.")
        if set(rule) - {"field", "operator", "value", "case_sensitive"}:
            raise ValueError(f"{prefix} contains unknown options.")
        if not isinstance(rule.get("field"), str) or not rule["field"].strip():
            raise ValueError(f"{prefix}.field must be a nonempty field reference name.")
        operator = rule.get("operator")
        if not isinstance(operator, str) or operator not in operators:
            raise ValueError(f"{prefix}.operator must be one of: {', '.join(sorted(operators))}.")
        if not isinstance(rule.get("case_sensitive", False), bool):
            raise ValueError(f"{prefix}.case_sensitive must be a boolean.")
        if operator in unary:
            if "value" in rule:
                raise ValueError(f"{prefix}: {operator} does not accept value.")
            continue
        if "value" not in rule:
            raise ValueError(f"{prefix}.value is required for {operator}.")
        value = rule["value"]
        if operator == "contains":
            if not isinstance(value, str):
                raise ValueError(f"{prefix}.value must be a string for contains.")
        elif operator in {"in", "not_in"}:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{prefix}.value must be a nonempty array for {operator}.")
            if any(entry is not None and not isinstance(entry, (str, int, float, bool)) for entry in value):
                raise ValueError(f"{prefix}.value entries must be strings, numbers, booleans or null.")
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"{prefix}.value must be a string, number, boolean or null.")
    return filters


def field_values_equal(actual, expected, case_sensitive=False):
    if isinstance(actual, str) and isinstance(expected, str):
        return actual == expected if case_sensitive else actual.casefold() == expected.casefold()
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def matches_field_filter(fields, rule):
    name = rule["field"].strip()
    operator = rule["operator"]
    present = name in fields
    actual = fields.get(name)
    if operator == "exists":
        return present
    if operator == "not_exists":
        return not present
    if operator == "is_empty":
        return not present or actual is None or (isinstance(actual, str) and not actual.strip())
    if not present:
        return False
    # Identity fields use the same uniqueName shown in the export.
    if isinstance(actual, dict) and "uniqueName" in actual:
        actual = actual["uniqueName"]
    if isinstance(actual, (dict, list)):
        return False
    expected = rule["value"]
    case_sensitive = rule.get("case_sensitive", False)
    if operator == "contains":
        if not isinstance(actual, str):
            return False
        return expected in actual if case_sensitive else expected.casefold() in actual.casefold()
    if operator in {"in", "not_in"}:
        matched = any(field_values_equal(actual, entry, case_sensitive) for entry in expected)
        return matched if operator == "in" else not matched
    matched = field_values_equal(actual, expected, case_sensitive)
    return matched if operator == "eq" else not matched


def matches_field_filters(work_item, filters):
    fields = work_item.get("fields", {})
    return all(matches_field_filter(fields, rule) for rule in filters)


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

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as file:
            json.dump({"query": wiql}, file)
            request_file = Path(file.name)

        try:
            response = run_az(
                [
                    az_command, "devops", "invoke",
                    "--org", org_url,
                    "--area", "wit",
                    "--resource", "wiql",
                    "--route-parameters", f"project={project}",
                    "--http-method", "POST",
                    "--api-version", "7.1",
                    "--query-parameters", f"$top={WORK_ITEM_QUERY_PAGE_SIZE}",
                    "--in-file", str(request_file),
                    "-o", "json",
                ]
            )
        finally:
            request_file.unlink(missing_ok=True)

        items = response["workItems"]

        if not items:
            break

        page_ids = [item["id"] for item in items]
        work_item_ids.extend(page_ids)

        next_last_id = page_ids[-1]
        if any(current <= previous for previous, current in zip([last_id] + page_ids, page_ids)):
            raise RuntimeError("Azure DevOps returned a non-advancing work item page.")

        last_id = next_last_id
        if len(page_ids) < WORK_ITEM_QUERY_PAGE_SIZE:
            break

    return work_item_ids


def build_parser():
    parser = argparse.ArgumentParser(description="Export Azure DevOps work items to Markdown.")
    parser.add_argument(
        "--title-filter",
        dest="title_filter",
        help="Filter titles using a case-insensitive regular expression; an empty string disables filtering.",
    )
    return parser


def build_work_item_link(org_url, project, work_item):
    work_item_id = work_item.get("id")
    project_path = quote(project, safe="")
    return f"{org_url}/{project_path}/_workitems/edit/{work_item_id}/"


def build_output_item(org_url, project, work_item):
    fields = work_item.get("fields", {})
    output_item = {
        "id": work_item.get("id"),
        "link-json": work_item.get("url"),
        "link": build_work_item_link(org_url, project, work_item),
        "type": fields.get("System.WorkItemType"),
        "state": fields.get("System.State"),
        "reason": fields.get("System.Reason"),
        "title": fields.get("System.Title"),
        "assignedTo": get_assigned_to(fields),
        "iterationPath": fields.get("System.IterationPath"),
        "areaPath": fields.get("System.AreaPath"),
        "description": fields.get("System.Description"),
        "comments": fields.get("Microsoft.VSTS.CMMI.Comments"),
        "acceptanceCriteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"),
        "additionalFields": build_additional_fields(fields),
    }
    return output_item


def simplify_field_value(value):
    if isinstance(value, dict):
        if "uniqueName" in value:
            return value["uniqueName"]
        return {key: simplify_field_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [simplify_field_value(nested) for nested in value]
    return value


def build_additional_fields(fields):
    return {
        name: simplify_field_value(value)
        for name, value in fields.items()
        if name not in SUMMARY_FIELD_NAMES and name != "System.History"
    }


def format_field_value(value):
    text = value if isinstance(value, str) and value else json.dumps(value, ensure_ascii=False)
    # Escape Markdown syntax so field contents stay inside their list item.
    text = re.sub(r"([\\`*_\[\]<>#!|~])", r"\\\1", text)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "  \n  ")


def build_field_lines(fields):
    return [f"- **{name}:** {format_field_value(value)}" for name, value in sorted(fields.items())]


def get_text_formatter(convert_html_to_markdown=False):
    if not isinstance(convert_html_to_markdown, bool):
        raise ValueError("convert_html_to_markdown must be a boolean (true or false).")
    if not convert_html_to_markdown:
        return str

    try:
        from bs4 import BeautifulSoup
        from markdownify import MarkdownConverter
    except ImportError as error:
        requirements = Path(__file__).with_name("requirements.txt")
        raise RuntimeError(
            "HTML conversion requires markdownify. Install it with: "
            f'python -m pip install -r "{requirements}"'
        ) from error

    class AdoMarkdownConverter(MarkdownConverter):
        def convert_li(self, element, text, parent_tags):
            rendered = super().convert_li(element, text, parent_tags)
            # A table needs its own line to render inside a Markdown list item.
            if text.lstrip().startswith("|"):
                rendered = re.sub(
                    r"^(\d+\.|-) (\|)",
                    lambda match: match[1] + "\n\n" + " " * (len(match[1]) + 1) + "|",
                    rendered,
                )
            return rendered

    def format_html(value):
        text = str(value)
        # Leave plain text and existing Markdown untouched.
        if not re.search(
            r"</?(?:div|p|span|br|b|strong|i|em|s|del|strike|a|img|ul|ol|li|"
            r"table|thead|tbody|tr|th|td|blockquote|pre|code|h[1-6]|hr)\b[^>]*>",
            text, re.IGNORECASE,
        ):
            return text
        soup = BeautifulSoup(text, "html.parser")
        for element in soup.find_all(["script", "style"]):
            element.decompose()
        for node in soup.find_all(string=True):
            if "\xa0" in node and not node.find_parent(["pre", "code"]):
                node.replace_with(str(node).replace("\xa0", " "))
        # ADO's pasted HTML sometimes places a nested list next to its parent item.
        for nested in soup.select("ol > ol, ol > ul, ul > ol, ul > ul"):
            previous = nested.find_previous_sibling()
            if previous is not None and previous.name == "li":
                previous.append(nested.extract())
        for element in reversed(soup.find_all(["div", "span", "p"])):
            if element.find_parent(["pre", "code"]):
                continue
            if not element.get_text(strip=True) and not element.find(
                ["img", "table", "hr", "pre", "code", "a"]
            ):
                if element.name == "span":
                    element.unwrap()
                else:
                    element.decompose()
        return AdoMarkdownConverter(
            heading_style="ATX", bullets="-", table_infer_header=True,
            keep_inline_images_in=["td", "th"], wrap=False,
        ).convert_soup(soup).strip()

    return format_html


def build_markdown_output(items, convert_html_to_markdown=False):
    format_text = get_text_formatter(convert_html_to_markdown)
    lines = ["# Azure DevOps Work Items", ""]

    for item in items:
        title = item.get("title") or "Untitled"
        lines.extend([
            f"## {item.get('id')}: {title}",
            "",
            f"- **Type:** {item.get('type') or ''}",
            f"- **State:** {item.get('state') or ''}",
            *([f"- **Reason:** {item['reason']}"] if item.get("reason") is not None else []),
            f"- **Assigned to:** {item.get('assignedTo') or ''}",
            f"- **Iteration path:** {item.get('iterationPath') or ''}",
            f"- **Area path:** {item.get('areaPath') or ''}",
            f"- **Link:** [{item.get('link')}]({item.get('link')})",
            *build_field_lines(item.get("additionalFields", {})),
            "",
        ])

        for heading, field in (
            ("Description", "description"),
            ("Acceptance criteria", "acceptanceCriteria"),
            ("Comments", "comments"),
        ):
            value = item.get(field)
            if value:
                text = format_text(value)
                if text:
                    lines.extend([f"### {heading}", "", text, ""])

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
                    format_text(text),
                    "",
                ])

    return "\n".join(lines)


def main():
    args = build_parser().parse_args()
    config = load_config()
    organization = config["organization"]
    project = config["project"]
    work_item_type = config["work_item_type"]
    title_filter = (
        args.title_filter if args.title_filter is not None else config.get("title_filter", "")
    )
    include_discussions = config.get("include_discussions", False)
    convert_html_to_markdown = config.get("convert_html_to_markdown", False)
    # Validate the option and dependency before making Azure DevOps requests.
    get_text_formatter(convert_html_to_markdown)
    excluded_sprints = parse_excluded_sprints(config.get("exclude_sprints", ""))
    included_sprints = parse_sprints(config.get("include_sprints", ""), "include_sprints")
    filters = validate_filters(config.get("filters", []))
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

    work_items = fetch_work_items(
        az_command, org_url, project, work_item_ids
    )
    work_items = [
        work_item
        for work_item in work_items
        if title_matches_filter(work_item, title_filter)
    ]

    if title_filter:
        print(f"Matched {len(work_items)} work items with title filter {title_filter!r}.")

    if included_sprints:
        work_items = [
            item for item in work_items if sprint_matches(item, included_sprints)
        ]
        print(f"Matched {len(work_items)} work items with included sprints.")

    if excluded_sprints:
        previous_count = len(work_items)
        work_items = [
            item for item in work_items if not sprint_is_excluded(item, excluded_sprints)
        ]
        print(f"Excluded {previous_count - len(work_items)} work items by sprint.")

    if filters:
        work_items = [item for item in work_items if matches_field_filters(item, filters)]
        print(f"Matched {len(work_items)} work items with field filters.")

    output = []
    for work_item in work_items:
        output_item = build_output_item(
            org_url, project, work_item
        )

        if include_discussions:
            print(f"Exporting discussion for work item {work_item['id']}...")
            output_item["discussion"] = fetch_discussion(
                az_command,
                org_url,
                project,
                work_item["id"]
            )

        output.append(output_item)

    output_file.write_text(
        build_markdown_output(output, convert_html_to_markdown=convert_html_to_markdown),
        encoding="utf-8",
    )

    print(f"Done: {output_file}")


if __name__ == "__main__":
    main()
