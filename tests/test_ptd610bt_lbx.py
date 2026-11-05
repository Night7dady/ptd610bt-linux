"""Synthetic LBX fixtures only; no personal labels and no Bluetooth access."""

import importlib.util
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from PIL import Image


path = Path(__file__).resolve().parents[1] / "scripts/ptd610bt_lbx.py"
spec = importlib.util.spec_from_file_location("ptd610_lbx", path)
lbx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lbx
spec.loader.exec_module(lbx)


def fixture(extra="", asset="icon.png", angle="0", paper_width="33.6pt"):
    xml = f'''<pt:document xmlns:pt="http://schemas.brother.info/ptouch/2007/lbx/main"
      xmlns:style="http://schemas.brother.info/ptouch/2007/lbx/style"
      xmlns:text="http://schemas.brother.info/ptouch/2007/lbx/text"
      xmlns:image="http://schemas.brother.info/ptouch/2007/lbx/image">
      <pt:body><style:sheet><style:paper orientation="landscape" width="{paper_width}"
        height="850pt" autoLength="true"/>
        <style:backGround x="5.6pt" y="4pt" width="67pt" height="25.6pt"/>
        <pt:objects><text:text>
          <pt:objectStyle x="41pt" y="4pt" width="200pt" height="25.6pt" angle="{angle}" flip="NONE"/>
          <text:ptFontInfo><text:logFont name="Arial" weight="400" italic="false"/>
            <text:fontExt size="8pt"/></text:ptFontInfo>
          <text:textControl control="FREE"/>
          <text:textAlign horizontalAlignment="JUSTIFY" verticalAlignment="CENTER"/>
          <text:textStyle lineSpace="-10" vertical="false"/>
          <pt:data>AA\nBB</pt:data>
          <text:stringItem charLen="3"><text:ptFontInfo>
            <text:logFont name="Arial" weight="700" italic="false"/>
            <text:fontExt size="8pt"/></text:ptFontInfo></text:stringItem>
        </text:text><image:image>
          <pt:objectStyle x="5.6pt" y="4pt" width="32pt" height="25.6pt" angle="0" flip="NONE"/>
          <image:imageStyle fileName="{asset}"><image:trimming flag="false"/></image:imageStyle>
        </image:image>{extra}</pt:objects>
      </style:sheet></pt:body></pt:document>'''
    logo = Image.new("RGBA", (40, 32), "white")
    for x in range(8, 30):
        for y in range(8, 24):
            logo.putpixel((x, y), (0, 0, 0, 255))
    png = io.BytesIO()
    logo.save(png, format="PNG")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("label.xml", xml)
        archive.writestr("icon.png", png.getvalue())
    return result.getvalue()


class LbxTests(unittest.TestCase):
    def test_auto_length_uses_background_not_850pt_ceiling(self):
        image, info = lbx.render_lbx(fixture())
        self.assertEqual(image.size, (168, 64))
        self.assertAlmostEqual(info["content_length_mm"], 67 * 25.4 / 72)
        self.assertEqual(image.mode, "1")

    def test_both_image_and_text_survive_layout(self):
        image, _ = lbx.render_lbx(fixture())
        self.assertGreater(image.crop((0, 0, 80, 64)).histogram()[0], 0)
        self.assertGreater(image.crop((88, 0, 168, 64)).histogram()[0], 0)

    def test_unsupported_objects_are_rejected_not_silently_omitted(self):
        with self.assertRaises(lbx.core.DiagnosticError):
            lbx.render_lbx(fixture(extra="<pt:unknown/>"))

    def test_rotated_layout_and_wrong_tape_are_refused(self):
        for data in (fixture(angle="90"), fixture(paper_width="68pt")):
            with self.assertRaises(lbx.core.DiagnosticError):
                lbx.render_lbx(data)

    def test_external_image_references_cannot_read_local_files(self):
        with self.assertRaises(lbx.core.DiagnosticError):
            lbx.render_lbx(fixture(asset="/etc/passwd"))

    def test_invalid_xml_and_entities_fail_closed(self):
        for xml in (b"<broken", b'<!DOCTYPE x [<!ENTITY a "text">]><x>&a;</x>'):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                archive.writestr("label.xml", xml)
            with self.assertRaises(lbx.core.DiagnosticError):
                lbx.render_lbx(data.getvalue())

    def test_preview_reads_source_without_changing_it_or_using_bluetooth(self):
        with tempfile.TemporaryDirectory(prefix="ptd610-lbx-test-") as tmp:
            source = Path(tmp) / "sample.lbx"
            output = Path(tmp) / "preview.png"
            data = fixture()
            source.write_bytes(data)
            with patch.object(lbx.core, "connect", side_effect=AssertionError("no radio")):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = lbx.main(["preview", str(source), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual(source.read_bytes(), data)
            self.assertTrue(output.is_file())

    def test_invalid_label_cannot_open_a_bluetooth_connection(self):
        with tempfile.TemporaryDirectory(prefix="ptd610-lbx-test-") as tmp:
            source = Path(tmp) / "unsupported.lbx"
            source.write_bytes(fixture(extra="<pt:unsupported/>"))
            with patch.object(lbx.core, "connect", side_effect=AssertionError("no radio")):
                with contextlib.redirect_stderr(io.StringIO()):
                    code = lbx.main(["print", str(source), "--mac", "00:00:00:00:00:00"])
            self.assertEqual(code, 2)

    def test_print_uses_verified_raster_and_always_closes_socket(self):
        with tempfile.TemporaryDirectory(prefix="ptd610-lbx-test-") as tmp:
            source = Path(tmp) / "sample.lbx"
            source.write_bytes(fixture())
            sock = MagicMock()
            session = MagicMock()
            with patch.object(lbx.core, "connect", return_value=sock):
                with patch.object(lbx.core, "Session", return_value=session):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(lbx.main(["print", str(source), "--mac", "00:00:00:00:00:00"]), 0)
            raster = session.print_image.call_args.args[0]
            self.assertEqual(raster.size, (168, 64))
            session.print_image.assert_called_once()
            sock.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
