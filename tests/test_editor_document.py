"""Offline document editing tests with synthetic in-memory labels."""
import importlib.util
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
import warnings

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from editor.document import LabelDocument
from editor.backend import core

fixture_spec = importlib.util.spec_from_file_location(
    'document_fixture', Path(__file__).with_name('test_ptd610bt_lbx.py'))
fixtures = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixtures)


class DocumentTests(unittest.TestCase):
    def test_new_label_renders_and_round_trips(self):
        doc = LabelDocument.new()
        self.assertFalse(doc.dirty)
        self.assertEqual(doc.render().height, 64)
        again = LabelDocument.from_bytes(doc.to_bytes())
        self.assertEqual(again.objects()[0].text, 'Label')
        self.assertAlmostEqual(again.length_mm, 35, places=4)
        self.assertFalse(again.dirty)

    def test_open_preserves_bytes_and_mixed_styles(self):
        data = fixtures.fixture()
        doc = LabelDocument.from_bytes(data)
        self.assertEqual(doc.to_bytes(), data)
        self.assertEqual(len(doc.objects()), 2)
        self.assertTrue(doc.objects()[0].mixed_style)
        self.assertFalse(doc.dirty)

    def test_text_edit_save_as_preserves_original_assets_and_metadata(self):
        base = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(fixtures.fixture())) as source:
            with zipfile.ZipFile(base, 'w') as target:
                for item in source.infolist(): target.writestr(item, source.read(item))
                target.writestr('prop.xml', b'<properties><value>kept</value></properties>')
                target.comment = b'archive metadata'
        data = base.getvalue()
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / 'original.lbx'
            output = Path(folder) / 'edited.lbx'
            original.write_bytes(data)
            doc = LabelDocument.load(original)
            key = doc.objects()[0].id
            doc.update_text(key, 'NEW', font_size=8, bold=True)
            self.assertTrue(doc.dirty)
            self.assertEqual(doc.objects()[0].id, key)
            self.assertFalse(doc.objects()[0].mixed_style)
            doc.save_as(output)
            self.assertFalse(doc.dirty)
            self.assertEqual(original.read_bytes(), data)
            reopened = LabelDocument.load(output)
            self.assertEqual(reopened.objects()[0].text, 'NEW')
            with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output) as target:
                self.assertEqual(source.read('icon.png'), target.read('icon.png'))
                self.assertEqual(source.read('prop.xml'), target.read('prop.xml'))
                self.assertEqual(source.comment, target.comment)
            with self.assertRaises((OSError, core.DiagnosticError)):
                doc.save_as(original)
            with self.assertRaises((OSError, core.DiagnosticError)):
                doc.save_as(output)

    def test_failed_edit_leaves_state_history_and_dirty_unchanged(self):
        doc = LabelDocument.from_bytes(fixtures.fixture())
        before = doc.to_bytes()
        key = doc.objects()[0].id
        with self.assertRaises(core.DiagnosticError):
            doc.update_text(key, 'much too long ' * 40, font_size=24)
        self.assertEqual(doc.to_bytes(), before)
        self.assertFalse(doc.can_undo)
        self.assertFalse(doc.dirty)

    def test_combined_update_is_atomic_and_one_undo_step(self):
        doc = LabelDocument.from_bytes(fixtures.fixture())
        key = doc.objects()[0].id
        before = doc.to_bytes()
        with self.assertRaises(core.DiagnosticError):
            doc.update_object(key, text='NEW', x_mm=-1)
        self.assertEqual(doc.to_bytes(), before)
        self.assertFalse(doc.can_undo)
        doc.update_object(key, text='NEW', x_mm=12)
        self.assertEqual(doc.objects()[0].text, 'NEW')
        self.assertAlmostEqual(doc.objects()[0].x_mm, 12)
        doc.undo()
        self.assertEqual(doc.to_bytes(), before)
        self.assertFalse(doc.can_undo)

    def test_staged_length_and_text_commit_is_one_undo_step(self):
        doc = LabelDocument.new()
        key = doc.objects()[0].id
        candidate = deepcopy(doc)
        candidate.set_length(40)
        candidate.update_text(key, 'Changed')
        self.assertAlmostEqual(doc.length_mm, 35)
        self.assertFalse(doc.dirty)
        doc.commit_candidate(candidate)
        self.assertAlmostEqual(doc.length_mm, 40)
        self.assertEqual(doc.objects()[0].text, 'Changed')
        self.assertTrue(doc.dirty)
        doc.undo()
        self.assertAlmostEqual(doc.length_mm, 35)
        self.assertEqual(doc.objects()[0].text, 'Label')
        self.assertFalse(doc.can_undo)
        self.assertFalse(doc.dirty)
        doc.redo()
        self.assertAlmostEqual(doc.length_mm, 40)
        self.assertEqual(doc.objects()[0].text, 'Changed')

    def test_staged_commit_preserves_earlier_history_and_rejects_invalid_candidate(self):
        doc = LabelDocument.new()
        key = doc.objects()[0].id
        doc.update_text(key, 'First')
        before = doc.to_bytes()
        candidate = deepcopy(doc)
        candidate.set_length(40)
        candidate.update_text(key, 'Changed')
        doc.commit_candidate(candidate)
        doc.undo()
        self.assertEqual(doc.to_bytes(), before)
        self.assertTrue(doc.can_undo)
        doc.undo()
        self.assertEqual(doc.objects()[0].text, 'Label')
        self.assertFalse(doc.dirty)
        candidate._data = b'not a valid LBX'
        before = doc.to_bytes()
        can_redo = doc.can_redo
        with self.assertRaises(core.DiagnosticError): doc.commit_candidate(candidate)
        self.assertEqual(doc.to_bytes(), before)
        self.assertFalse(doc.can_undo)
        self.assertEqual(doc.can_redo, can_redo)
        self.assertFalse(doc.dirty)

    def test_undo_redo_restore_ids_and_saved_dirty_state(self):
        doc = LabelDocument.from_bytes(fixtures.fixture())
        key = doc.objects()[0].id
        doc.update_text(key, 'ONE')
        self.assertTrue(doc.can_undo)
        doc.undo()
        self.assertEqual(doc.objects()[0].text, 'AA\nBB')
        self.assertEqual(doc.objects()[0].id, key)
        self.assertFalse(doc.dirty)
        doc.redo()
        self.assertEqual(doc.objects()[0].text, 'ONE')
        self.assertTrue(doc.dirty)
        doc.undo()
        doc.update_text(key, 'TWO')
        self.assertFalse(doc.can_redo)

    def test_add_image_and_text_then_remove_and_undo(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'example.png'
            Image.new('RGB', (12, 8), 'black').save(image)
            doc = LabelDocument.new()
            text = doc.add_text('A')
            picture = doc.add_image(image)
            self.assertEqual(len(doc.objects()), 3)
            self.assertEqual(doc.objects()[-1].id, picture)
            self.assertEqual(doc.objects()[-1].kind, 'image')
            doc.remove(text)
            self.assertEqual(len(doc.objects()), 2)
            doc.undo()
            self.assertIn(text, [obj.id for obj in doc.objects()])
            self.assertEqual(LabelDocument.from_bytes(doc.to_bytes()).render().height, 64)

    def test_geometry_is_relative_to_band_and_invalid_moves_are_transactional(self):
        doc = LabelDocument.from_bytes(fixtures.fixture())
        item = doc.objects()[1]
        self.assertAlmostEqual(item.x_mm, 0)
        doc.update_geometry(item.id, 1, 0, item.width_mm, item.height_mm)
        self.assertAlmostEqual(doc.objects()[1].x_mm, 1)
        before = doc.to_bytes()
        with self.assertRaises(core.DiagnosticError):
            doc.update_geometry(item.id, -1, 0, item.width_mm, item.height_mm)
        self.assertEqual(doc.to_bytes(), before)

    def test_resize_validates_content_and_empty_document_is_rejected(self):
        doc = LabelDocument.new()
        doc.set_length(40)
        self.assertAlmostEqual(doc.length_mm, 40)
        with self.assertRaises(core.DiagnosticError): doc.set_length(200)
        with self.assertRaises(core.DiagnosticError): doc.remove(doc.objects()[0].id)
        self.assertEqual(len(doc.objects()), 1)

    def test_refuse_unsupported_duplicates_and_dtd_including_utf16(self):
        with self.assertRaises(core.DiagnosticError):
            LabelDocument.from_bytes(fixtures.fixture(extra='<pt:unsupported/>'))
        duplicate = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(duplicate, 'w') as archive:
                archive.writestr('label.xml', '<a/>')
                archive.writestr('label.xml', '<b/>')
        with self.assertRaises(core.DiagnosticError):
            LabelDocument.from_bytes(duplicate.getvalue())
        for xml in [b'<!DOCTYPE x [<!ENTITY v "oops">]><x>&v;</x>',
                    '<!DOCTYPE x [<!ENTITY v "oops">]><x>&v;</x>'.encode('utf-16')]:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive: archive.writestr('label.xml', xml)
            with self.assertRaises(core.DiagnosticError): LabelDocument.from_bytes(data.getvalue())

    def test_unknown_xml_metadata_comments_and_asset_bytes_survive_edit(self):
        data = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(fixtures.fixture())) as source:
            with zipfile.ZipFile(data, 'w') as target:
                for item in source.infolist():
                    value = source.read(item)
                    if item.filename == 'label.xml':
                        value = value.replace(b'<pt:body>', b'<pt:body><!--kept--><pt:custom key="value">metadata</pt:custom>')
                    target.writestr(item, value)
        doc = LabelDocument.from_bytes(data.getvalue())
        doc.update_text(doc.objects()[0].id, 'NEW')
        with zipfile.ZipFile(io.BytesIO(doc.to_bytes())) as archive:
            xml = archive.read('label.xml')
            self.assertIn(b'<!--kept-->', xml)
            self.assertIn(b'key="value"', xml)
            self.assertIn(b'>metadata<', xml)

    def test_excessive_xml_depth_fails_cleanly_before_serialization(self):
        xml = '<x>' * 70 + '</x>' * 70
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('label.xml', xml)
        with self.assertRaisesRegex(core.DiagnosticError, 'structural limits'):
            LabelDocument.from_bytes(data.getvalue())

    def test_traversal_entry_is_rejected_without_extracting(self):
        data = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(fixtures.fixture())) as source:
            with zipfile.ZipFile(data, 'w') as target:
                for item in source.infolist(): target.writestr(item, source.read(item))
                target.writestr('../outside.txt', b'not allowed')
        with self.assertRaises(core.DiagnosticError): LabelDocument.from_bytes(data.getvalue())


if __name__ == '__main__': unittest.main()
