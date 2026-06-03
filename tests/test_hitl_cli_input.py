"""Regression tests for HITL CLI input handling."""

from __future__ import annotations

import os
import select


def test_hitl_timeout_reader_does_not_consume_late_input(monkeypatch):
    """A timed-out HITL prompt must not leave a stale reader on stdin."""
    import main

    read_fd, write_fd = os.pipe()
    read_file = os.fdopen(read_fd, "r")
    try:
        monkeypatch.setattr(main.sys, "stdin", read_file)
        monkeypatch.setattr(main.console, "print", lambda *args, **kwargs: None)

        result = main._read_console_line_with_timeout("You > ", 0.01)
        assert result is None

        os.write(write_fd, b"late task\n")
        ready, _, _ = select.select([read_file], [], [], 0.2)
        assert ready
        assert read_file.readline() == "late task\n"
    finally:
        read_file.close()
        os.close(write_fd)

