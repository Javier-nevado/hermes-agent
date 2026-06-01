"""Tests for tools.file_tools — deliver_file_tool."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.file_tools import (
    deliver_file_tool,
    get_and_clear_recently_written,
)


class TestDeliverFileTool:
    def setup_method(self):
        # Clear the queue before each test
        get_and_clear_recently_written()

    def test_existing_file_queued(self, tmp_path):
        f = tmp_path / "report.html"
        f.write_text("<h1>Hello</h1>")

        result = deliver_file_tool(str(f))
        data = json.loads(result)

        assert data["success"] is True
        assert data["filename"] == "report.html"
        assert data["size_bytes"] > 0

        queued = get_and_clear_recently_written()
        assert str(f) in queued

    def test_nonexistent_file_error(self):
        result = deliver_file_tool("/nonexistent/path/report.html")
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_directory_error(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()

        result = deliver_file_tool(str(d))
        data = json.loads(result)
        assert "error" in data
        assert "not a regular file" in data["error"].lower()

    def test_deduplication(self, tmp_path):
        f = tmp_path / "report.html"
        f.write_text("<h1>Hello</h1>")

        deliver_file_tool(str(f))
        deliver_file_tool(str(f))

        queued = get_and_clear_recently_written()
        assert queued.count(str(f)) == 1

    def test_size_display_kb(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_bytes(b"x" * 2048)

        result = deliver_file_tool(str(f))
        data = json.loads(result)
        assert "KB" in data["size_human"]

    def test_size_display_bytes(self, tmp_path):
        f = tmp_path / "tiny.txt"
        f.write_bytes(b"hi")

        result = deliver_file_tool(str(f))
        data = json.loads(result)
        assert "bytes" in data["size_human"]

    def test_sensitive_path_blocked(self):
        result = deliver_file_tool("/etc/passwd")
        data = json.loads(result)
        assert "error" in data

    def test_queue_cleared_after_read(self, tmp_path):
        f = tmp_path / "report.html"
        f.write_text("<h1>Hello</h1>")

        deliver_file_tool(str(f))
        first = get_and_clear_recently_written()
        assert len(first) == 1

        second = get_and_clear_recently_written()
        assert len(second) == 0

    def test_missing_path_param(self):
        result = deliver_file_tool("")
        data = json.loads(result)
        assert "error" in data
