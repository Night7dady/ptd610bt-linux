"""Bounded, transactional editing of the LBX layouts supported by our renderer.

Files are read-only inputs. Saving always creates a new file exclusively. The
original raster/protocol implementation remains the single rendering backend.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import io
import math
import os
from pathlib import Path, PurePosixPath
import stat
import xml.etree.ElementTree as ET
import uuid
import zipfile

from PIL import Image

from .backend import core, legacy

NS = legacy.NS
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

MAX_ARCHIVE_BYTES = 10_000_000
MAX_EXPANDED_BYTES = 20_000_000
MAX_ENTRIES = 64
MAX_HISTORY_BYTES = 32_000_000
MAX_HISTORY_STEPS = 30
PT_PER_MM = 72 / 25.4
BAND_HEIGHT_PT = 25.6


def _tag(prefix: str, name: str) -> str:
    return f'{{{NS[prefix]}}}{name}'


def _dimension(value: float) -> str:
    if not math.isfinite(value):
        raise core.DiagnosticError('Dimensions must be finite numbers')
    return f'{value:.8f}'.rstrip('0').rstrip('.') + 'pt'


class _SafeTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise core.DiagnosticError('XML document types and entities are unsupported')


@dataclass(frozen=True)
class LabelObject:
    id: str
    kind: str
    text: str = ''
    font_size: float = 8.0
    bold: bool = False
    mixed_style: bool = False
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0


@dataclass(frozen=True)
class _Snapshot:
    data: bytes
    ids: tuple[str, ...]


def _read_archive(data: bytes):
    """Read bounded entries without extracting, and reject DTDs before rendering."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise core.DiagnosticError('LBX exceeds the 10 MB file limit')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (len(entries) > MAX_ENTRIES or len({item.filename for item in entries}) != len(entries)
                    or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES):
                raise core.DiagnosticError('Excessive or ambiguous LBX archive')
            for item in entries:
                name = PurePosixPath(item.filename)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (name.is_absolute() or '..' in name.parts or '\\' in item.filename
                        or not item.filename or any(ord(c) < 32 for c in item.orig_filename)
                        or item.flag_bits & 1
                        or mode not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise core.DiagnosticError('Unsafe or encrypted LBX archive entry')
            parts = [(copy.copy(item), archive.read(item)) for item in entries]
            comment = archive.comment
        xml = next(payload for item, payload in parts if item.filename == 'label.xml')
        if len(xml) > 1_000_000:
            raise core.DiagnosticError('LBX XML exceeds the editor limit')
        root = ET.fromstring(xml, parser=ET.XMLParser(
            target=_SafeTreeBuilder(insert_comments=True, insert_pis=True)))
        stack = [(root, 1)]
        count = 0
        while stack:
            element, depth = stack.pop()
            count += 1
            if count > 20_000 or depth > 64:
                raise core.DiagnosticError('LBX XML exceeds editor structural limits')
            stack.extend((child, depth + 1) for child in element)
        return parts, comment, root
    except (zipfile.BadZipFile, KeyError, StopIteration, ET.ParseError, ValueError,
            OSError, RuntimeError, OverflowError) as exc:
        raise core.DiagnosticError(f'Cannot open LBX: {exc}') from exc


def _write_archive(parts, comment: bytes, root: ET.Element) -> bytes:
    xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for item, payload in parts:
            archive.writestr(copy.copy(item), xml if item.filename == 'label.xml' else payload)
        archive.comment = comment
    return output.getvalue()


def _fingerprint(data: bytes) -> bytes:
    parts, comment, root = _read_archive(data)
    digest = hashlib.sha256()
    for item, payload in sorted(parts, key=lambda item: item[0].filename):
        digest.update(item.filename.encode('utf-8'))
        digest.update(b'\0')
        if item.filename == 'label.xml':
            payload = ET.canonicalize(ET.tostring(root, encoding='unicode'),
                                      rewrite_prefixes=True).encode('utf-8')
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)
    digest.update(comment)
    return digest.digest()


def _objects_node(root):
    return legacy.needed(root, 'pt:body/style:sheet/pt:objects')


def _object_elements(root):
    return [child for child in _objects_node(root) if isinstance(child.tag, str)]


def _band(root):
    return legacy.needed(root, 'pt:body/style:sheet/style:backGround')


def _text_element(text: str, width_pt: float) -> ET.Element:
    element = ET.Element(_tag('text', 'text'))
    ET.SubElement(element, _tag('pt', 'objectStyle'),
                  x='0pt', y='0pt', width=_dimension(width_pt),
                  height='25.6pt', angle='0', flip='NONE')
    info = ET.SubElement(element, _tag('text', 'ptFontInfo'))
    ET.SubElement(info, _tag('text', 'logFont'), name='Arial', weight='400', italic='false')
    ET.SubElement(info, _tag('text', 'fontExt'), size='8pt')
    ET.SubElement(element, _tag('text', 'textControl'), control='FREE', autoLF='false')
    ET.SubElement(element, _tag('text', 'textAlign'),
                  horizontalAlignment='LEFT', verticalAlignment='CENTER')
    ET.SubElement(element, _tag('text', 'textStyle'), lineSpace='-10', vertical='false')
    ET.SubElement(element, _tag('pt', 'data')).text = text
    return element


class LabelDocument:
    def __init__(self, data: bytes, source_path: Path | None = None):
        _, _, root = _read_archive(data)
        self._image, self._info = self._validated_render(data)
        self._data = data
        self._ids = tuple(f'object-{i + 1}' for i in range(len(_object_elements(root))))
        self._next_id = len(self._ids) + 1
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []
        self.source_path = Path(source_path).absolute() if source_path is not None else None
        self._saved_fingerprint = _fingerprint(data)

    @staticmethod
    def _validated_render(data):
        _read_archive(data)
        try:
            return legacy.render_lbx(data, allow_empty=True)
        except (ValueError, OSError, OverflowError, Image.DecompressionBombError) as exc:
            raise core.DiagnosticError(f'Cannot render LBX: {exc}') from exc

    @classmethod
    def load(cls, path: Path | str) -> LabelDocument:
        source = Path(path)
        with source.open('rb') as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        return cls.from_bytes(data, source_path=source)

    @classmethod
    def from_bytes(cls, data: bytes, source_path=None) -> LabelDocument:
        return cls(bytes(data), source_path=source_path)

    @classmethod
    def new(cls, length_mm: float = 35.0) -> LabelDocument:
        cls._check_length(length_mm)
        root = ET.Element(_tag('pt', 'document'))
        body = ET.SubElement(root, _tag('pt', 'body'))
        sheet = ET.SubElement(body, _tag('style', 'sheet'))
        ET.SubElement(sheet, _tag('style', 'paper'), orientation='landscape', width='33.6pt',
                      height='850pt', autoLength='true')
        ET.SubElement(sheet, _tag('style', 'backGround'), x='0pt', y='0pt',
                      width=_dimension(length_mm * PT_PER_MM), height='25.6pt')
        objects_node = ET.SubElement(sheet, _tag('pt', 'objects'))
        objects_node.append(_text_element('Label', length_mm * PT_PER_MM))
        item = zipfile.ZipInfo('label.xml')
        item.compress_type = zipfile.ZIP_DEFLATED
        return cls.from_bytes(_write_archive([(item, b'')], b'', root))

    @staticmethod
    def _check_length(length_mm):
        if not math.isfinite(length_mm) or not 32 * 25.4 / core.DPI <= length_mm <= 80:
            raise core.DiagnosticError('Content length must be between 4.52 and 80 mm')

    @property
    def length_mm(self) -> float:
        return float(self._info['content_length_mm'])

    @property
    def dirty(self) -> bool:
        return _fingerprint(self._data) != self._saved_fingerprint

    @property
    def is_blank(self) -> bool:
        """Whether the rendered raster has no printable black pixels."""
        return self._image.histogram()[0] == 0

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def render(self) -> Image.Image:
        return self._image.copy()

    def info(self) -> dict:
        return dict(self._info)

    def to_bytes(self) -> bytes:
        return self._data

    def objects(self) -> list[LabelObject]:
        _, _, root = _read_archive(self._data)
        band = _band(root)
        bx, by = legacy.pt(band.get('x')), legacy.pt(band.get('y'))
        result = []
        for key, obj in zip(self._ids, _object_elements(root)):
            geo = legacy.needed(obj, 'pt:objectStyle')
            geometry = dict(x_mm=(legacy.pt(geo.get('x')) - bx) / PT_PER_MM,
                            y_mm=(legacy.pt(geo.get('y')) - by) / PT_PER_MM,
                            width_mm=legacy.pt(geo.get('width')) / PT_PER_MM,
                            height_mm=legacy.pt(geo.get('height')) / PT_PER_MM)
            if obj.tag == _tag('text', 'text'):
                base = legacy.font_spec(legacy.needed(obj, 'text:ptFontInfo'))
                styles = {legacy.font_spec(span.find('text:ptFontInfo', NS), base)
                          for span in obj.findall('text:stringItem', NS)}
                result.append(LabelObject(key, 'text', text=legacy.needed(obj, 'pt:data').text or '',
                                          font_size=base[1], bold=base[0] == 700,
                                          mixed_style=any(style != base for style in styles), **geometry))
            else:
                result.append(LabelObject(key, 'image', **geometry))
        return result

    def _find(self, root, object_id):
        try:
            return _object_elements(root)[self._ids.index(object_id)]
        except (ValueError, IndexError) as exc:
            raise core.DiagnosticError('The selected object no longer exists') from exc

    @staticmethod
    def _trim_history(history):
        while len(history) > MAX_HISTORY_STEPS or sum(len(s.data) for s in history) > MAX_HISTORY_BYTES:
            history.pop(0)

    def _commit(self, parts, comment, root, ids=None):
        data = _write_archive(parts, comment, root)
        image, info = self._validated_render(data)
        ids = tuple(self._ids if ids is None else ids)
        if _fingerprint(data) == _fingerprint(self._data) and ids == self._ids:
            return
        self._undo.append(_Snapshot(self._data, self._ids))
        self._trim_history(self._undo)
        self._redo.clear()
        self._data, self._ids, self._image, self._info = data, ids, image, info

    def commit_candidate(self, candidate: LabelDocument):
        """Adopt staged edits in one undo step, retaining this document's baseline.

        The caller may validate several edits on a deepcopy first. Intermediate
        candidate history is discarded; source path and saved-state tracking stay
        attached to this original document. Failed validation changes nothing.
        """
        if not isinstance(candidate, LabelDocument):
            raise core.DiagnosticError('Expected a staged label document')
        parts, comment, root = _read_archive(candidate.to_bytes())
        ids = candidate._ids
        if (len(ids) != len(_object_elements(root)) or len(set(ids)) != len(ids)
                or any(not isinstance(key, str) or not key for key in ids)):
            raise core.DiagnosticError('The staged object identities are invalid')
        self._commit(parts, comment, root, ids=ids)
        self._next_id = max(self._next_id, candidate._next_id)

    def set_length(self, mm: float):
        self._check_length(mm)
        parts, comment, root = _read_archive(self._data)
        _band(root).set('width', _dimension(mm * PT_PER_MM))
        self._commit(parts, comment, root)

    def update_object(self, object_id: str, *, text=None, font_size=None, bold=None,
                      x_mm=None, y_mm=None, width_mm=None, height_mm=None):
        """Apply all fields as one validated mutation and one undo step."""
        parts, comment, root = _read_archive(self._data)
        obj = self._find(root, object_id)
        if text == '' and obj.tag == _tag('text', 'text'):
            _objects_node(root).remove(obj)
            self._commit(parts, comment, root,
                         ids=tuple(key for key in self._ids if key != object_id))
            return
        if any(value is not None for value in (text, font_size, bold)):
            if obj.tag != _tag('text', 'text'):
                raise core.DiagnosticError('Select a text object to edit text')
            base = legacy.font_spec(legacy.needed(obj, 'text:ptFontInfo'))
            text = legacy.needed(obj, 'pt:data').text if text is None else text
            font_size = base[1] if font_size is None else font_size
            bold = base[0] == 700 if bold is None else bold
            if not isinstance(text, str) or not text or len(text) > 4096:
                raise core.DiagnosticError('Text must contain 1 to 4096 characters')
            if not math.isfinite(font_size) or not 4 <= font_size <= 24:
                raise core.DiagnosticError('Font size must be between 4 and 24 pt')
            legacy.needed(obj, 'pt:data').text = text
            font = legacy.needed(obj, 'text:ptFontInfo')
            legacy.needed(font, 'text:logFont').set('weight', '700' if bold else '400')
            legacy.needed(font, 'text:fontExt').set('size', _dimension(font_size))
            for child in obj.findall('text:stringItem', NS):
                obj.remove(child)
        if any(value is not None for value in (x_mm, y_mm, width_mm, height_mm)):
            geo, band = legacy.needed(obj, 'pt:objectStyle'), _band(root)
            bx, by = legacy.pt(band.get('x')), legacy.pt(band.get('y'))
            x_mm = (legacy.pt(geo.get('x')) - bx) / PT_PER_MM if x_mm is None else x_mm
            y_mm = (legacy.pt(geo.get('y')) - by) / PT_PER_MM if y_mm is None else y_mm
            width_mm = legacy.pt(geo.get('width')) / PT_PER_MM if width_mm is None else width_mm
            height_mm = legacy.pt(geo.get('height')) / PT_PER_MM if height_mm is None else height_mm
            if (any(not math.isfinite(n) for n in (x_mm, y_mm, width_mm, height_mm))
                    or min(x_mm, y_mm) < 0 or min(width_mm, height_mm) <= 0):
                raise core.DiagnosticError('Position must be nonnegative and size must be positive')
            geo.set('x', _dimension(bx + x_mm * PT_PER_MM))
            geo.set('y', _dimension(by + y_mm * PT_PER_MM))
            geo.set('width', _dimension(width_mm * PT_PER_MM))
            geo.set('height', _dimension(height_mm * PT_PER_MM))
        self._commit(parts, comment, root)

    def update_text(self, object_id: str, text: str, font_size: float = 8, bold: bool = False):
        self.update_object(object_id, text=text, font_size=font_size, bold=bold)

    def update_geometry(self, object_id: str, x_mm: float, y_mm: float,
                        width_mm: float, height_mm: float):
        self.update_object(object_id, x_mm=x_mm, y_mm=y_mm,
                           width_mm=width_mm, height_mm=height_mm)

    def add_text(self, text: str = 'Label') -> str:
        parts, comment, root = _read_archive(self._data)
        node = _text_element(text, legacy.pt(_band(root).get('width')))
        band = _band(root)
        geo = legacy.needed(node, 'pt:objectStyle')
        geo.set('x', band.get('x'))
        geo.set('y', band.get('y'))
        _objects_node(root).append(node)
        key = f'object-{self._next_id}'
        self._commit(parts, comment, root, ids=(*self._ids, key))
        self._next_id += 1
        return key

    def add_image(self, path: Path | str) -> str:
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise core.DiagnosticError('Image exceeds the 10 MB input limit')
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 5_000_000:
                    raise core.DiagnosticError('Image exceeds the 5 megapixel limit')
                output = io.BytesIO()
                image.convert('RGBA').save(output, format='PNG')
                aspect = image.width / image.height
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise core.DiagnosticError(f'Cannot add image: {exc}') from exc
        parts, comment, root = _read_archive(self._data)
        band = _band(root)
        width = min(legacy.pt(band.get('width')), BAND_HEIGHT_PT * aspect)
        height = width / aspect
        node = ET.SubElement(_objects_node(root), _tag('image', 'image'))
        ET.SubElement(node, _tag('pt', 'objectStyle'), x=band.get('x'),
                      y=_dimension(legacy.pt(band.get('y')) + (BAND_HEIGHT_PT - height) / 2),
                      width=_dimension(width), height=_dimension(height), angle='0', flip='NONE')
        filename = f'editor-image-{uuid.uuid4().hex}.png'
        style = ET.SubElement(node, _tag('image', 'imageStyle'), fileName=filename)
        ET.SubElement(style, _tag('image', 'trimming'), flag='false')
        item = zipfile.ZipInfo(filename)
        item.compress_type = zipfile.ZIP_DEFLATED
        parts.append((item, output.getvalue()))
        key = f'object-{self._next_id}'
        self._commit(parts, comment, root, ids=(*self._ids, key))
        self._next_id += 1
        return key

    def get_image(self, object_id: str) -> Image.Image:
        """Return an independent RGBA copy of the original embedded bitmap."""
        parts, _, root = _read_archive(self._data)
        obj = self._find(root, object_id)
        if obj.tag != _tag('image', 'image'):
            raise core.DiagnosticError('Select an image object')
        name = legacy.needed(obj, 'image:imageStyle').get('fileName')
        payload = next((data for item, data in parts if item.filename == name), None)
        if payload is None:
            raise core.DiagnosticError('The embedded image is missing')
        try:
            with Image.open(io.BytesIO(payload)) as image:
                if image.width * image.height > 5_000_000:
                    raise core.DiagnosticError('Image exceeds the 5 megapixel limit')
                return image.convert('RGBA').copy()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise core.DiagnosticError(f'Cannot read embedded image: {exc}') from exc

    def crop_image(self, object_id: str, box: tuple[int, int, int, int]):
        """Crop source pixels, retaining print scale and the object's x/y position.

        A fresh PNG entry is created so other objects sharing the original asset
        remain unchanged. The whole bitmap/geometry replacement is one undo step.
        """
        if (not isinstance(box, (tuple, list)) or len(box) != 4
                or any(not isinstance(value, int) or isinstance(value, bool) for value in box)):
            raise core.DiagnosticError('Crop bounds must be four integer pixel coordinates')
        image = self.get_image(object_id)
        left, top, right, bottom = box
        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
            raise core.DiagnosticError('Crop bounds must form a nonempty rectangle inside the image')
        if tuple(box) == (0, 0, image.width, image.height):
            return
        cropped = image.crop(box)
        output = io.BytesIO()
        cropped.save(output, format='PNG')
        parts, comment, root = _read_archive(self._data)
        obj = self._find(root, object_id)
        style = legacy.needed(obj, 'image:imageStyle')
        filename = f'editor-image-{uuid.uuid4().hex}.png'
        entry = zipfile.ZipInfo(filename)
        entry.compress_type = zipfile.ZIP_DEFLATED
        parts.append((entry, output.getvalue()))
        style.set('fileName', filename)
        trimming = style.find('image:trimming', NS)
        if trimming is None:
            trimming = ET.SubElement(style, _tag('image', 'trimming'))
        trimming.set('flag', 'false')
        geometry = legacy.needed(obj, 'pt:objectStyle')
        geometry.set('width', _dimension(legacy.pt(geometry.get('width')) * cropped.width / image.width))
        geometry.set('height', _dimension(legacy.pt(geometry.get('height')) * cropped.height / image.height))
        self._commit(parts, comment, root)

    def remove(self, object_id: str):
        parts, comment, root = _read_archive(self._data)
        _objects_node(root).remove(self._find(root, object_id))
        self._commit(parts, comment, root, ids=tuple(key for key in self._ids if key != object_id))

    def _restore(self, snapshot):
        image, info = self._validated_render(snapshot.data)
        self._data, self._ids = snapshot.data, snapshot.ids
        self._image, self._info = image, info

    def undo(self):
        if self._undo:
            self._redo.append(_Snapshot(self._data, self._ids))
            self._trim_history(self._redo)
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(_Snapshot(self._data, self._ids))
            self._trim_history(self._undo)
            self._restore(self._redo.pop())

    def save_as(self, path: Path | str):
        target = Path(path)
        if self.source_path is not None and target.absolute() == self.source_path:
            raise core.DiagnosticError('Choose a new file; the source LBX is read-only')
        identity = None
        try:
            with target.open('xb') as stream:
                identity = os.fstat(stream.fileno())
                stream.write(self._data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            if identity is not None:
                try:
                    current = target.lstat()
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        target.unlink()
                except OSError:
                    pass
            raise
        self.source_path = target.absolute()
        self._saved_fingerprint = _fingerprint(self._data)
