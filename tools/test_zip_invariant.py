import unittest
import tempfile
import os
import zipfile
import hashlib
from pathlib import Path

# Add repo root to path
import sys
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.build_cp13a1_complete_payload_hotfix import write_complete_payload_zip, enumerate_stage_files

class TestZipInvariant(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.stage = self.temp_path / "stage"
        self.stage.mkdir()
        self.output_zip = self.temp_path / "output.zip"

        # Base files
        self.required_files = [
            "app/main.py",
            "tools/offline_translation_worker.py",
            "tools/ocr_runtime_worker.py",
            "tools/storage_preflight.py",
            "operator/translation_config.env"
        ]
        
        for rel in self.required_files:
            p = self.stage / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"content")

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_manifest(self, overrides=None):
        components = []
        for rel in self.required_files:
            c = {
                "component": rel.split("/")[-1].split(".")[0],
                "relative_path": rel,
                "type": "file",
                "required": True,
                "size": 7,
                "sha256": hashlib.sha256(b"content").hexdigest()
            }
            components.append(c)
            
        manifest = {"components": components}
        if overrides:
            for c in manifest["components"]:
                if c["relative_path"] in overrides:
                    c.update(overrides[c["relative_path"]])
        return manifest

    def test_1_normal_stage(self):
        manifest = self.create_manifest()
        write_complete_payload_zip(self.stage, self.output_zip, manifest)
        
        with zipfile.ZipFile(self.output_zip, "r") as zf:
            names = set(zf.namelist())
            for req in self.required_files:
                self.assertIn(req, names)

    def test_2_manifest_requires_missing_file(self):
        manifest = self.create_manifest()
        # Add a missing file to manifest
        manifest["components"].append({
            "component": "missing_file",
            "relative_path": "tools/missing_file.py",
            "type": "file",
            "required": True,
            "size": 10,
            "sha256": "fakehash"
        })
        with self.assertRaisesRegex(RuntimeError, "Manifest required file missing in ZIP: tools/missing_file.py"):
            write_complete_payload_zip(self.stage, self.output_zip, manifest)

    def test_3_file_deleted_after_inventory(self):
        # Mock os.walk so it returns the list, but we delete the file before zip creates
        import tools.build_cp13a1_complete_payload_hotfix as mod
        original_walk = os.walk
        
        def fake_walk(*args, **kwargs):
            # Let it yield once, then delete the file
            for item in original_walk(*args, **kwargs):
                yield item
                
            # After os.walk finishes in enumerate_stage_files, we delete a file
            (self.stage / "tools/offline_translation_worker.py").unlink()
            
        mod.os.walk = fake_walk
        try:
            manifest = self.create_manifest()
            # FileNotFoundError should happen when archive.write() is called
            with self.assertRaises(FileNotFoundError):
                write_complete_payload_zip(self.stage, self.output_zip, manifest)
        finally:
            mod.os.walk = original_walk

    def test_4_duplicate_relative_zip_entry(self):
        # We can't really have duplicate files on filesystem, so mock enumerate_stage_files
        import tools.build_cp13a1_complete_payload_hotfix as mod
        original_enumerate = mod.enumerate_stage_files
        
        def fake_enumerate(*args, **kwargs):
            res = original_enumerate(*args, **kwargs)
            res.append("tools/offline_translation_worker.py") # duplicate
            return res
            
        mod.enumerate_stage_files = fake_enumerate
        try:
            manifest = self.create_manifest()
            with self.assertRaisesRegex(RuntimeError, "ZIP contains duplicate names|duplicate names"):
                write_complete_payload_zip(self.stage, self.output_zip, manifest)
        finally:
            mod.enumerate_stage_files = original_enumerate

    def test_5_scan_error_fails(self):
        import tools.build_cp13a1_complete_payload_hotfix as mod
        
        def fake_walk(*args, **kwargs):
            onerror = kwargs.get('onerror')
            if onerror:
                onerror(OSError("fake permission denied"))
            yield from []
            
        original_walk = mod.os.walk
        mod.os.walk = fake_walk
        try:
            manifest = self.create_manifest()
            with self.assertRaisesRegex(RuntimeError, "Scan error in enumerate_stage_files: fake permission denied"):
                write_complete_payload_zip(self.stage, self.output_zip, manifest)
        finally:
            mod.os.walk = original_walk

    def test_6_manifest_hash_mismatch(self):
        manifest = self.create_manifest(overrides={
            "tools/offline_translation_worker.py": {"sha256": "badhash"}
        })
        with self.assertRaisesRegex(RuntimeError, "Manifest hash mismatch for tools/offline_translation_worker.py"):
            write_complete_payload_zip(self.stage, self.output_zip, manifest)

if __name__ == "__main__":
    unittest.main(verbosity=2)
