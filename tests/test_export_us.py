import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "export_us", Path(__file__).resolve().parents[1] / "export" / "export_us.py"
)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class ExportTests(unittest.TestCase):
    def test_all_fields_are_always_requested_and_rendered_below_title(self):
        items = [
            {"id": 1, "fields": {
                "System.Title": "Task 1", "System.Description": "Unique description text",
                "System.History": "Latest discussion entry",
                "Custom.Analyst": {"uniqueName": "ana@example.com", "imageUrl": "avatar-url"},
                "Custom.ProgressPercentage": 0, "Custom.Enabled": False,
                "Custom.Empty": "", "Custom.Null": None,
            }},
            {"id": 2, "fields": {"System.Title": "Task 2", "Custom.Other": "Other value"}},
        ]
        # Old configurations cannot disable the new always-on behavior.
        for legacy_mode in [None, False, True]:
            config = dict(organization="org", project="project", work_item_type="Task",
                          output_file="test.md")
            if legacy_mode is not None:
                config["include_all_fields"] = legacy_mode

            def respond(command):
                request = json.loads(Path(command[command.index("--in-file") + 1])
                                     .read_text(encoding="utf-8"))
                self.assertEqual(request["ids"], [1, 2])
                self.assertNotIn("fields", request)
                return {"value": items}

            with self.subTest(legacy_mode=legacy_mode), \
                 patch("sys.argv", ["export_us.py"]), \
                 patch.object(export, "load_config", return_value=config), \
                 patch.object(export, "get_az_command", return_value="az"), \
                 patch.object(export, "fetch_work_item_ids", return_value=[1, 2]), \
                 patch.object(export, "run_az", side_effect=respond), \
                 patch.object(export, "fetch_discussion") as discussion, \
                 patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write, \
                 patch("builtins.print"):
                export.main()
            markdown = write.call_args.args[0]
            discussion.assert_not_called()
            self.assertIn("## 1: Task 1", markdown)
            self.assertIn("## 2: Task 2", markdown)
            for line in ["- **Custom.Analyst:** ana@example.com",
                         "- **Custom.ProgressPercentage:** 0", "- **Custom.Enabled:** false",
                         '- **Custom.Empty:** ""', "- **Custom.Null:** null",
                         "- **Custom.Other:** Other value"]:
                self.assertEqual(markdown.count(line), 1)
            self.assertLess(markdown.index("- **Custom.ProgressPercentage:**"),
                            markdown.index("### Description"))
            self.assertGreater(markdown.index("- **Custom.Other:**"), markdown.index("## 2:"))
            self.assertEqual(markdown.count("Unique description text"), 1)
            self.assertNotIn("System.Description", markdown)
            self.assertNotIn("System.History", markdown)
            self.assertNotIn("Latest discussion entry", markdown)
            self.assertNotIn("avatar-url", markdown)
            self.assertNotIn("### Additional fields", markdown)
            self.assertNotIn("```json", markdown)

    def test_multiline_values_remain_in_their_bullet(self):
        value = "First line\n# Heading\n```json\nLast line"
        rendered = export.build_field_lines({"Custom.Notes": value})[0]
        self.assertTrue(rendered.startswith("- **Custom.Notes:** First line"))
        self.assertTrue(all(line.startswith("  ") for line in rendered.splitlines()[1:]))
        self.assertNotIn("```", rendered)
        self.assertEqual(export.format_field_value({"count": 0}), '{"count": 0}')

    def test_additional_fields_preserve_other_objects_without_mutating_source(self):
        fields = {"System.Title": "Task", "Custom.Reviewers": [
            {"displayName": "Ana", "uniqueName": "ana@example.com"}],
            "Custom.Data": {"count": 0, "enabled": False},
            "Custom.UnknownIdentity": {"displayName": "Unknown", "id": "123"}}
        original = json.loads(json.dumps(fields))
        self.assertEqual(export.build_additional_fields(fields), {
            "Custom.Reviewers": ["ana@example.com"],
            "Custom.Data": {"count": 0, "enabled": False},
            "Custom.UnknownIdentity": {"displayName": "Unknown", "id": "123"}})
        self.assertEqual(fields, original)

    def test_no_additional_section_when_only_standard_fields_exist(self):
        item = export.build_output_item("org", "project", {
            "id": 1, "fields": {"System.Title": "Task", "System.Description": "Description text"}
        })
        markdown = export.build_markdown_output([item])
        self.assertNotIn("### Additional fields", markdown)
        self.assertEqual(markdown.count("Description text"), 1)

    def test_sprint_exclusions_use_exact_names(self):
        excluded = export.parse_excluded_sprints(" Sprint 1 | SPRINT 2 || ")
        for path, expected in [
            ("Project\\Release\\sprint 1", True),
            ("Project\\Sprint 2", True),
            ("Project\\Sprint 10", False),
            ("Project\\Sprint 1\\Child", False),
            (None, False),
        ]:
            with self.subTest(path=path):
                item = {"fields": {"System.IterationPath": path}}
                self.assertEqual(export.sprint_is_excluded(item, excluded), expected)
        self.assertEqual(export.parse_excluded_sprints(" | "), set())
        with self.assertRaisesRegex(ValueError, "exclude_sprints"):
            export.parse_excluded_sprints(["Sprint 1"])

    def test_excluded_sprints_are_not_exported_or_fetched_for_discussion(self):
        config = dict(organization="org", project="project", work_item_type="Task",
                      output_file="test.md", exclude_sprints="Sprint 1|Sprint 2",
                      include_discussions=True)
        items = [
            {"id": i, "fields": {"System.Title": f"Task {i}",
                                  "System.IterationPath": f"Project\\Sprint {i}"}}
            for i in range(1, 4)
        ]
        with patch("sys.argv", ["export_us.py"]), \
             patch.object(export, "load_config", return_value=config), \
             patch.object(export, "get_az_command", return_value="az"), \
             patch.object(export, "fetch_work_item_ids", return_value=[1, 2, 3]), \
             patch.object(export, "fetch_work_items", return_value=items), \
             patch.object(export, "fetch_discussion", return_value=[]) as discussion, \
             patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write, \
             patch("builtins.print"):
            export.main()
        markdown = write.call_args.args[0]
        self.assertNotIn("Task 1", markdown)
        self.assertNotIn("Task 2", markdown)
        self.assertIn("Task 3", markdown)
        discussion.assert_called_once_with("az", "https://dev.azure.com/org", "project", 3)

    def test_ten_thousand_items_and_temporary_file_cleanup(self):
        ids = list(range(2, 20001, 2))
        request_files = []

        def respond(command):
            self.assertIn("$top=1000", command)
            self.assertEqual(command[command.index("--resource") + 1], "wiql")
            request_file = Path(command[command.index("--in-file") + 1])
            request_files.append(request_file)
            query = json.loads(request_file.read_text(encoding="utf-8"))["query"]
            last_id = int(re.search(r"\[System.Id\] > (\d+)", query)[1])
            return {"workItems": [{"id": i} for i in ids if i > last_id][:1000]}

        with patch.object(export, "run_az", side_effect=respond) as az:
            self.assertEqual(export.fetch_work_item_ids("az", "org", "project", "Task"), ids)
        self.assertEqual(az.call_count, 11)
        self.assertTrue(all(not path.exists() for path in request_files))

    def test_query_file_removed_on_failure(self):
        request_files = []

        def fail(command):
            request_files.append(Path(command[command.index("--in-file") + 1]))
            raise RuntimeError("Request failed")

        with patch.object(export, "run_az", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "Request failed"):
                export.fetch_work_item_ids("az", "org", "project", "Task")
        self.assertFalse(request_files[0].exists())

    def test_non_advancing_page_is_rejected(self):
        with patch.object(export, "run_az", return_value={"workItems": [{"id": 0}]}):
            with self.assertRaisesRegex(RuntimeError, "non-advancing"):
                export.fetch_work_item_ids("az", "org", "project", "Task")

    def test_discussion_paginates_over_updates_without_comments(self):
        comment = {"fields": {"System.History": {"newValue": "Later comment"}}}
        with patch.object(export, "run_az", side_effect=[
            {"value": [{"fields": {}}] * 200},
            {"value": [comment]},
            {"value": []},
        ]) as az:
            result = export.fetch_discussion("az", "org", "project", 1)
        self.assertEqual([entry["discussion"] for entry in result], ["Later comment"])
        for call, skip in zip(az.call_args_list, [0, 200, 201]):
            self.assertIn(f"$skip={skip}", call.args[0])
            self.assertIn("$top=200", call.args[0])

    def test_missing_work_items_fail_with_ids(self):
        with patch.object(export, "run_az", return_value={"value": [{"id": 1}]}):
            with self.assertRaisesRegex(RuntimeError, "omitted work item IDs: 2, 3"):
                export.fetch_work_items("az", "org", "project", [1, 2, 3])

    def test_complete_batch_succeeds(self):
        items = [{"id": 2}, {"id": 1}]
        with patch.object(export, "run_az", return_value={"value": items}):
            self.assertEqual(export.fetch_work_items("az", "org", "project", [1, 2]), items)

    def test_cli_filter_overrides_config_including_empty_string(self):
        config = dict(organization="org", project="project", work_item_type="Task",
                      output_file="test.md", title_filter="Birdie")
        item = {"id": 1, "fields": {"System.Title": "Other task"}}
        for arguments, included in [([], False), (["--title-filter="], True),
                                    (["--title-filter=Other"], True)]:
            with self.subTest(arguments=arguments), \
                 patch("sys.argv", ["export_us.py"] + arguments), \
                 patch.object(export, "load_config", return_value=config), \
                 patch.object(export, "get_az_command", return_value="az"), \
                 patch.object(export, "fetch_work_item_ids", return_value=[1]), \
                 patch.object(export, "fetch_work_items", return_value=[item]), \
                 patch.object(Path, "mkdir"), patch.object(Path, "write_text") as write, \
                 patch("builtins.print"):
                export.main()
                self.assertEqual("Other task" in write.call_args.args[0], included)


if __name__ == "__main__":
    unittest.main()
