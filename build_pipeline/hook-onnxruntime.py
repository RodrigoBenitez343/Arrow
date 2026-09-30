import os
from PyInstaller.utils.hooks import collect_data_files, copy_metadata, get_package_paths

hiddenimports = [
    "onnxruntime.capi._ld_preload",
    "onnxruntime.capi._pybind_state",
    "onnxruntime.capi.onnxruntime_inference_collection",
    "onnxruntime.capi.onnxruntime_pybind11_state",
]

datas = []
datas += collect_data_files(
    "onnxruntime",
    includes=[
        "LICENSE*",
        "Privacy.md",
        "ThirdPartyNotices.txt",
    ],
    excludes=["**/__pycache__/**"],
)

try:
    datas += copy_metadata("onnxruntime")
except Exception:
    pass

binaries = []
try:
    _pkg_base, _pkg_dir = get_package_paths("onnxruntime")
    _capi_dir = os.path.join(_pkg_dir, "capi")
    if os.path.isdir(_capi_dir):
        for name in os.listdir(_capi_dir):
            lower = name.lower()
            if lower.endswith(".dll") or lower.endswith(".pyd"):
                binaries.append((os.path.join(_capi_dir, name), os.path.join("onnxruntime", "capi")))
except Exception:
    pass

