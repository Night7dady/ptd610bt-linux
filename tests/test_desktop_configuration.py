import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "desktop_configuration", Path(__file__).resolve().parents[1] / "tools" / "configure_desktop.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class DesktopConfigurationTests(unittest.TestCase):
    def test_one_file_no_shell_and_retained_console(self):
        result = config.desktop_entry(Path('/tmp/stable project/scripts/ptd610bt_lbx.py'),
                                      '00:00:00:00:00:00')
        self.assertIn('/usr/bin/konsole --hold --separate -e ', result)
        self.assertIn('"/tmp/stable project/scripts/ptd610bt_lbx.py" print %f ', result)
        self.assertEqual(result.count('%f'), 1)
        self.assertNotIn('%F', result)
        self.assertNotIn('sh -c', result)
        self.assertIn('MimeType=application/x-brother-lbx;', result)

    def test_desktop_argument_escaping(self):
        self.assertEqual(config.desktop_argument('/tmp/a%/label.py'), '"/tmp/a%%/label.py"')
        self.assertEqual(config.desktop_argument('$'), '"\\\\$"')
        self.assertEqual(config.desktop_argument('"'), '"\\\\\""')
        self.assertEqual(config.desktop_argument('\\'), '"\\\\\\\\"')
        self.assertEqual(config.desktop_argument('`'), '"\\\\`"')
        for value in ['', 'bad\npath', 'bad\rpath', '\x00bad']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.desktop_argument(value)

    def test_mac_and_channel_validation(self):
        for mac in ['invalid', '00:00:00:00:00', '00:00:00:00:00:00;echo bad',
                    '00:00:00:00:00:00\nExec=bad']:
            with self.subTest(mac=mac), self.assertRaises(ValueError):
                config.desktop_entry(Path('/tmp/script.py'), mac)
        for channel in [0, 31, -1]:
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                config.desktop_entry(Path('/tmp/script.py'), '00:00:00:00:00:00', channel)
        with self.assertRaises(ValueError):
            config.desktop_entry(Path('relative.py'), '00:00:00:00:00:00')

    def test_mime_is_custom_lbx_zip_is_only_parent(self):
        root = ET.fromstring(config.MIME_XML)
        ns = {'m': 'http://www.freedesktop.org/standards/shared-mime-info'}
        types = root.findall('m:mime-type', ns)
        self.assertEqual([entry.get('type') for entry in types], ['application/x-brother-lbx'])
        self.assertEqual(types[0].find('m:sub-class-of', ns).get('type'), 'application/zip')
        glob = types[0].find('m:glob', ns)
        self.assertEqual(glob.get('pattern'), '*.lbx')
        self.assertIsNone(glob.get('case-sensitive'))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            out = parent / 'generated'
            before = list(parent.iterdir())
            with patch.object(Path, 'mkdir', side_effect=AssertionError('dry run attempted write')):
                result = config.generate(out, Path('/tmp/script.py'), '00:00:00:00:00:00', dry_run=True)
            self.assertTrue(result['dry_run'])
            self.assertFalse(result['installed'])
            self.assertFalse(result['printer_contacted'])
            self.assertEqual(list(parent.iterdir()), before)

    def test_new_directory_and_refuse_repeated_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'generated'
            config.generate(out, Path('/tmp/script.py'), '00:00:00:00:00:00')
            self.assertEqual({p.name for p in out.iterdir()},
                             {'ptd610bt-lbx.xml', 'ptd610bt-lbx-print.desktop'})
            expected = {p.name: p.read_bytes() for p in out.iterdir()}
            with self.assertRaisesRegex(ValueError, 'must not already exist'):
                config.generate(out, Path('/tmp/different.py'), '00:00:00:00:00:00')
            self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, expected)
            self.assertEqual(out.stat().st_mode & 0o777, 0o700)

    def test_invalid_input_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'generated'
            with self.assertRaises(ValueError):
                config.generate(out, Path('/tmp/script.py'), 'invalid')
            self.assertFalse(out.exists())

    def test_symlink_output_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'generated'
            out.symlink_to(Path(folder) / 'missing')
            with self.assertRaises(ValueError):
                config.generate(out, Path('/tmp/script.py'), '00:00:00:00:00:00')


if __name__ == '__main__':
    unittest.main()
