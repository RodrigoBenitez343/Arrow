import os
import sys
import ctypes

# Set KMP_DUPLICATE_LIB_OK to avoid issues with multiple OpenMP runtimes
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

# Get the base directories for searching DLLs
meipass = getattr(sys, '_MEIPASS', None)
exe_dir = os.path.dirname(sys.executable)

# We look for DLLs in several possible locations relative to the executable or bundle root
candidate_bases = []
if meipass:
    candidate_bases.append(meipass)
    if os.path.basename(meipass).lower() != '_internal':
        candidate_bases.append(os.path.join(meipass, '_internal'))
candidate_bases.append(exe_dir)
candidate_bases.append(os.path.join(exe_dir, '_internal'))

dll_paths = []
for base in candidate_bases:
    if not os.path.isdir(base):
        continue
    
    # Add the base directory itself (where we put DLLs in '.')
    if base not in dll_paths:
        dll_paths.append(base)
    
    # Torch DLLs
    torch_lib = os.path.join(base, 'torch', 'lib')
    if os.path.isdir(torch_lib) and torch_lib not in dll_paths:
        dll_paths.append(torch_lib)
        
    # Torchvision DLLs
    torchvision_path = os.path.join(base, 'torchvision')
    if os.path.isdir(torchvision_path) and torchvision_path not in dll_paths:
        dll_paths.append(torchvision_path)
    
    paddle_libs = os.path.join(base, 'paddle', 'libs')
    if os.path.isdir(paddle_libs) and paddle_libs not in dll_paths:
        dll_paths.append(paddle_libs)
    
    paddle_base = os.path.join(base, 'paddle', 'base')
    if os.path.isdir(paddle_base) and paddle_base not in dll_paths:
        dll_paths.append(paddle_base)
    
    paddle_site_libs = os.path.join(base, 'paddle.libs')
    if os.path.isdir(paddle_site_libs) and paddle_site_libs not in dll_paths:
        dll_paths.append(paddle_site_libs)

    onnxruntime_capi = os.path.join(base, 'onnxruntime', 'capi')
    if os.path.isdir(onnxruntime_capi) and onnxruntime_capi not in dll_paths:
        dll_paths.append(onnxruntime_capi)

    onnxruntime_root = os.path.join(base, 'onnxruntime')
    if os.path.isdir(onnxruntime_root) and onnxruntime_root not in dll_paths:
        dll_paths.append(onnxruntime_root)

# Add discovered paths to the Windows DLL search path
if hasattr(os, 'add_dll_directory'):
    _dll_dir_handles = []
    for p in dll_paths:
        try:
            _dll_dir_handles.append(os.add_dll_directory(p))
        except Exception:
            pass

# Also update the PATH environment variable as a fallback
old_path = os.environ.get('PATH', '')
new_path_entries = [p for p in dll_paths if p not in old_path]
if new_path_entries:
    os.environ['PATH'] = os.pathsep.join(new_path_entries + [old_path] if old_path else new_path_entries)

# Pre-load critical DLLs in a specific order to "prime" the loader
# This prevents WinError 1114 by ensuring dependencies are loaded before torch/PyQt conflicts can occur.

def load_dll(name, search_paths):
    # print(f"Attempting to load {name}...")
    for base in search_paths:
        dll_path = os.path.join(base, name)
        if os.path.exists(dll_path):
            try:
                # Use RTLD_GLOBAL if available to make symbols available to other DLLs
                # On Windows, this is essentially ignored by ctypes, but we keep the logic
                flags = getattr(os, 'RTLD_GLOBAL', 0)
                handle = ctypes.CDLL(dll_path, mode=flags)
                # print(f"Successfully loaded {name} from {base}")
                return handle
            except Exception as e:
                # print(f"Failed to load {name} from {base}: {e}")
                pass
    return None

# Step 1: VC Redists (The foundation)
for redist in ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll']:
    load_dll(redist, dll_paths)

# Step 2: OpenMP (Common point of failure for WinError 1114)
load_dll('libiomp5md.dll', dll_paths)

# Step 3 & 4: Torch core libraries
load_dll('c10.dll', dll_paths)
load_dll('torch_cpu.dll', dll_paths)

# Step 5: ONNX Runtime core (providers are often loaded dynamically)
load_dll('onnxruntime_providers_shared.dll', dll_paths)
load_dll('onnxruntime.dll', dll_paths)

# Finally, trigger an early import of torch while we are still in the hook
# to ensure it initializes before the main application starts loading PyQt.
try:
    import torch
    # print("Torch successfully initialized in runtime hook.")
except Exception as e:
    # print(f"Torch initialization failed in hook: {e}")
    pass
