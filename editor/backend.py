"""Load the verified raster and Bluetooth backend from fixed project paths."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / 'scripts' / 'ptd610bt_lbx.py'
_spec = spec_from_file_location('editor._verified_lbx', _path)
if _spec is None or _spec.loader is None:
    raise ImportError('The complete project checkout is required')
legacy = module_from_spec(_spec)
sys.modules[_spec.name] = legacy
_spec.loader.exec_module(legacy)
core = legacy.core
