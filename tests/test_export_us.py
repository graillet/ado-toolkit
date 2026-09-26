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
