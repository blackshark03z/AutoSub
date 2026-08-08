import os
import json
import tempfile
import queue
from pathlib import Path
from unittest import mock
import pytest
from app.services.caption_analysis_runtime import _analysis_worker_entry

class DummyException(Exception):
    code = "DUMMY_ERROR"

def test_diagnostic_persistence_exception():
    with tempfile.TemporaryDirectory() as td:
        result_queue = queue.Queue()
        
        def mock_create(*args, **kwargs):
            progress = kwargs.get("progress")
            if progress:
                progress._state["analysis_stage"] = "ocr"
                progress._state["ocr_batches_completed"] = 16
            raise DummyException("API_KEY=12345secret and Authorization: Bearer abcdef failed")
            
        with mock.patch("app.services.source_caption_translation.create_source_caption_translation", side_effect=mock_create):
            _analysis_worker_entry(result_queue, "fake.mp4", td, False, None)
            
        diag_file = Path(td) / "diagnostics" / "worker_exception_diag.json"
        assert diag_file.is_file(), "Diagnostic artifact should be created"
        
        diag_data = json.loads(diag_file.read_text(encoding="utf-8"))
        
        # 1. Exception test
        assert diag_data["exception_type"] == "DummyException"
        
        # 2. Progress snapshot test
        assert diag_data["analysis_stage"] == "ocr"
        assert diag_data["ocr_batches_completed"] == 16
        
        # 3. Sanitization test
        assert "12345secret" not in diag_data["exception_message"]
        assert "API_KEY=***" in diag_data["exception_message"]
        assert "abcdef" not in diag_data["exception_message"]
        assert "Authorization: ***" in diag_data["exception_message"]
        
        # Original error preserved
        msg = result_queue.get_nowait()
        assert msg["status"] == "error"
        assert msg["code"] == "DUMMY_ERROR"

def test_diagnostic_persistence_write_failure():
    with tempfile.TemporaryDirectory() as td:
        result_queue = queue.Queue()
        
        def mock_create(*args, **kwargs):
            raise DummyException("Fail")
            
        with mock.patch("app.services.source_caption_translation.create_source_caption_translation", side_effect=mock_create):
            
            original_replace = Path.replace
            def mock_replace(self, target, *args, **kwargs):
                if "worker_exception_diag" in str(target):
                    raise Exception("Disk full")
                return original_replace(self, target, *args, **kwargs)
                
            with mock.patch("pathlib.Path.replace", side_effect=mock_replace, autospec=True):
                _analysis_worker_entry(result_queue, "fake.mp4", td, False, None)
                
        # 4. Diagnostic write failure should not swallow exception
        msg = result_queue.get_nowait()
        assert msg["status"] == "error"
        assert msg["code"] == "DUMMY_ERROR"
        assert "Fail" in msg["message"]

def test_diagnostic_persistence_success():
    with tempfile.TemporaryDirectory() as td:
        result_queue = queue.Queue()
        
        def mock_create(*args, **kwargs):
            return {"test": "success"}
            
        with mock.patch("app.services.source_caption_translation.create_source_caption_translation", side_effect=mock_create):
            _analysis_worker_entry(result_queue, "fake.mp4", td, False, None)
            
        # 5. Success path should not create artifact
        diag_file = Path(td) / "diagnostics" / "worker_exception_diag.json"
        assert not diag_file.exists()
        
        msg = result_queue.get_nowait()
        assert msg["status"] == "ok"
        assert msg["result"] == {"test": "success"}
