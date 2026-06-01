"""Tests for agent.tool_result_classification — phantom file-write detection."""

from __future__ import annotations

import pytest

from agent.tool_result_classification import (
    extract_deliverable_filenames_from_text,
    file_mutation_result_landed,
)


# ── Existing verifier tests ──────────────────────────────────────────────

class TestFileMutationResultLanded:
    def test_write_file_success(self):
        assert file_mutation_result_landed("write_file", '{"bytes_written": 42}') is True

    def test_write_file_error(self):
        assert file_mutation_result_landed("write_file", '{"error": "nope"}') is False

    def test_patch_success(self):
        assert file_mutation_result_landed("patch", '{"success": true}') is True

    def test_patch_failure(self):
        assert file_mutation_result_landed("patch", '{"success": false}') is False

    def test_unknown_tool(self):
        assert file_mutation_result_landed("read_file", '{"ok": true}') is False

    def test_non_string_result(self):
        assert file_mutation_result_landed("write_file", {"bytes_written": 1}) is False

    def test_invalid_json(self):
        assert file_mutation_result_landed("write_file", "not json") is False


# ── Phantom file-write detection tests ───────────────────────────────────

class TestExtractDeliverableFilenames:
    def test_simple_html(self):
        text = "Here is your report: content-plan.html"
        assert extract_deliverable_filenames_from_text(text) == ["content-plan.html"]

    def test_multiple_files(self):
        text = "Created report.pdf and summary.md for you."
        assert extract_deliverable_filenames_from_text(text) == ["report.pdf", "summary.md"]

    def test_backtick_wrapped(self):
        text = "File saved: `analysis.docx`"
        assert extract_deliverable_filenames_from_text(text) == ["analysis.docx"]

    def test_quote_wrapped(self):
        text = 'I saved "data.csv" and \'report.xlsx\''
        result = extract_deliverable_filenames_from_text(text)
        assert "data.csv" in result
        assert "report.xlsx" in result

    def test_path_prefix_stripped(self):
        text = "Wrote to /tmp/workspace/output.html"
        assert extract_deliverable_filenames_from_text(text) == ["output.html"]

    def test_deduplication(self):
        text = "See report.pdf and also report.pdf again"
        assert extract_deliverable_filenames_from_text(text) == ["report.pdf"]

    def test_case_insensitive(self):
        text = "Created Report.HTML and data.PDF"
        result = extract_deliverable_filenames_from_text(text)
        assert "report.html" in result
        assert "data.pdf" in result

    def test_empty_input(self):
        assert extract_deliverable_filenames_from_text("") == []
        assert extract_deliverable_filenames_from_text(None) == []

    def test_no_deliverable_files(self):
        text = "Here is a picture.png of the event"
        assert extract_deliverable_filenames_from_text(text) == []

    def test_json_extension(self):
        text = "Saved config.json to disk"
        assert extract_deliverable_filenames_from_text(text) == ["config.json"]

    def test_python_extension(self):
        text = "Created script.py for you"
        assert extract_deliverable_filenames_from_text(text) == ["script.py"]

    def test_yaml_extension(self):
        text = "Config written to docker-compose.yaml"
        assert extract_deliverable_filenames_from_text(text) == ["docker-compose.yaml"]

    def test_bare_extension_not_matched(self):
        text = "The format is .html and the type is pdf"
        assert extract_deliverable_filenames_from_text(text) == []

    def test_real_world_model_response(self):
        text = (
            "Done! Here's the complete content plan with all visual assets embedded:\n\n"
            "**week23-content-plan.html** (4.4MB — self-contained, all images embedded)\n\n"
            "The file shows every post with its visual placement. "
            "I've also saved the carousel as carousel.html."
        )
        result = extract_deliverable_filenames_from_text(text)
        assert "week23-content-plan.html" in result
        assert "carousel.html" in result

    def test_hyphenated_filename(self):
        text = "Your file: my-awesome-report-2026.pdf"
        assert extract_deliverable_filenames_from_text(text) == [
            "my-awesome-report-2026.pdf"
        ]
