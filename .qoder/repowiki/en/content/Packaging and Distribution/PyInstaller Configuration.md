# PyInstaller Configuration

<cite>
**Referenced Files in This Document**
- [LoOperApp.spec](file://LoOperApp.spec)
- [hook-onnxruntime.py](file://hook-onnxruntime.py)
- [onnxruntime_runtime_hook.py](file://onnxruntime_runtime_hook.py)
- [torch_runtime_hook.py](file://torch_runtime_hook.py)
- [requirements.txt](file://requirements.txt)
- [LoOper/main.py](file://LoOper/main.py)
- [LoOper/player/computer_vision.py](file://LoOper/player/computer_vision.py)
- [LoOper/player/ocr_engine.py](file://LoOper/player/ocr_engine.py)
- [LoOper/player/Computer_vision/model_vision.py](file://LoOper/player/Computer_vision/model_vision.py)
- [LoOper/player/Computer_vision/layoutlmv3_layer.py](file://LoOper/player/Computer_vision/layoutlmv3_layer.py)
- [LoOper/sandbox_agent.py](file://LoOper/sandbox_agent.py)
</cite>

## Update Summary
**Changes Made**
- Enhanced LoOperApp.spec configuration to bundle sandbox_agent.py file for compiled builds
- Added documentation for sandbox execution architecture and RDP session integration
- Updated data collection strategies to include lightweight sandbox agent bridge
- Expanded troubleshooting guide with sandbox-related deployment considerations

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Sandbox Execution Integration](#sandbox-execution-integration)
7. [Dependency Analysis](#dependency-analysis)
8. [Performance Considerations](#performance-considerations)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Conclusion](#conclusion)

## Introduction
This document explains the PyInstaller configuration used to build cross-platform executables for the LoOperV2 project. It focuses on the complete build specification, hidden imports, data and binary collection strategies, and runtime hooks for AI/ML frameworks such as ONNX Runtime and PyTorch. The configuration now includes enhanced support for sandbox execution through the bundled sandbox_agent.py file, enabling secure remote desktop protocol (RDP) sessions with isolated automation capabilities. The document covers how the build integrates with OCR assets and computer vision models, and how the runtime hooks resolve DLL dependencies and library path issues.

## Project Structure
The build system centers on a single PyInstaller spec file that orchestrates:
- Hidden imports for PyQt5, NodeGraphQt, AI libraries, and computer vision frameworks
- Data collection for AI models, OCR assets, application resources, and sandbox agent components
- Binary collection and filtering for ONNX Runtime and PyTorch
- Runtime hooks for DLL resolution and pre-loading
- Sandbox agent integration for RDP session automation

```mermaid
graph TB
Spec["LoOperApp.spec<br/>Build specification"] --> Hooks["Runtime Hooks<br/>onnxruntime_runtime_hook.py<br/>torch_runtime_hook.py"]
Spec --> Collect["Data/Binary Collection<br/>hiddenimports + datas + binaries"]
Collect --> Models["AI Models Bundled<br/>PaddleOCR models"]
Collect --> OCR["OCR Assets Bundled<br/>OCR engines and configs"]
Collect --> CV["Computer Vision Models<br/>Ultralytics/YOLO + LayoutLMv3"]
Collect --> Sandbox["Sandbox Agent<br/>sandbox_agent.py for RDP"]
Spec --> Exe["Executable Output<br/>LoOper.exe"]
```

**Diagram sources**
- [LoOperApp.spec:463-515](file://LoOperApp.spec#L463-L515)
- [onnxruntime_runtime_hook.py:1-34](file://onnxruntime_runtime_hook.py#L1-L34)
- [torch_runtime_hook.py:1-118](file://torch_runtime_hook.py#L1-L118)
- [LoOper/sandbox_agent.py:1-157](file://LoOper/sandbox_agent.py#L1-L157)

**Section sources**
- [LoOperApp.spec:27-87](file://LoOperApp.spec#L27-L87)
- [LoOperApp.spec:89-131](file://LoOperApp.spec#L89-L131)
- [LoOperApp.spec:463-515](file://LoOperApp.spec#L463-L515)

## Core Components
- Build specification (spec): Defines hidden imports, data/binary collections, and runtime hooks.
- ONNX Runtime hook: Declares hidden imports and collects license/data files plus binaries.
- ONNX Runtime runtime hook: Prepares the DLL search path and pre-loads providers.
- Torch runtime hook: Manages DLL search paths, pre-loads critical libraries, and resolves conflicts with PyQt and other runtimes.
- Requirements: Lists all dependencies including PyTorch, ONNX Runtime, Ultralytics, PaddleOCR, and OpenCV.
- Sandbox agent: Lightweight HTTP server for RDP session automation with secure action execution.

Key responsibilities:
- Ensure all dynamic imports are discoverable at analysis time via hidden imports.
- Bundle AI/OCR models and resources into the final executable.
- Resolve DLL conflicts and missing dependencies at runtime using hooks.
- Provide secure sandbox execution for remote desktop automation.

**Section sources**
- [LoOperApp.spec:27-87](file://LoOperApp.spec#L27-L87)
- [LoOperApp.spec:121-131](file://LoOperApp.spec#L121-L131)
- [hook-onnxruntime.py:4-39](file://hook-onnxruntime.py#L4-L39)
- [onnxruntime_runtime_hook.py:7-34](file://onnxruntime_runtime_hook.py#L7-L34)
- [torch_runtime_hook.py:5-118](file://torch_runtime_hook.py#L5-L118)
- [requirements.txt:89-127](file://requirements.txt#L89-L127)
- [LoOper/sandbox_agent.py:1-157](file://LoOper/sandbox_agent.py#L1-L157)

## Architecture Overview
The build pipeline integrates three major phases:
1) Analysis-time discovery: hidden imports and collected data/binary assets
2) Bundle-time assembly: datas/binaries normalization and exclusion rules
3) Runtime-time resolution: DLL search path setup and pre-loading

```mermaid
sequenceDiagram
participant Dev as "Developer"
participant Spec as "LoOperApp.spec"
participant HookONNX as "hook-onnxruntime.py"
participant HookORT as "onnxruntime_runtime_hook.py"
participant HookTorch as "torch_runtime_hook.py"
participant Sandbox as "sandbox_agent.py"
participant Exec as "LoOper.exe"
Dev->>Spec : Run pyinstaller with spec
Spec->>HookONNX : Collect onnxruntime data/binaries
Spec->>Spec : hiddenimports + datas + binaries
Spec->>Sandbox : Bundle sandbox_agent.py for RDP
Spec-->>Exec : Build executable with bundled assets
Exec->>HookORT : Initialize at runtime
Exec->>HookTorch : Initialize at runtime
HookORT->>HookORT : Set PATH and add_dll_directory
HookORT->>HookORT : Pre-load onnxruntime providers
HookTorch->>HookTorch : Add torch/torchvision/paddle/onnxruntime paths
HookTorch->>HookTorch : Pre-load VC++/OpenMP/DLLs in order
Sandbox->>Sandbox : Start HTTP server for RDP automation
Exec-->>Dev : Executable runs with resolved dependencies
```

**Diagram sources**
- [LoOperApp.spec:420-423](file://LoOperApp.spec#L420-L423)
- [hook-onnxruntime.py:22-37](file://hook-onnxruntime.py#L22-L37)
- [onnxruntime_runtime_hook.py:21-34](file://onnxruntime_runtime_hook.py#L21-L34)
- [torch_runtime_hook.py:13-74](file://torch_runtime_hook.py#L13-L74)
- [LoOper/sandbox_agent.py:106-157](file://LoOper/sandbox_agent.py#L106-L157)

## Detailed Component Analysis

### LoOperApp.spec: Build Specification and Hidden Imports
- Hidden imports: Explicitly declare PyQt5, NodeGraphQt, AI/ML libraries, and system-level packages to avoid missing imports at analysis time.
- Data collection: Adds application resources, schedules, icons, the entire Computer_vision directory, and the sandbox_agent.py file to ensure all assets are included.
- Binary collection: Uses PyInstaller's collection utilities for complex packages and manually adds ONNX Runtime binaries and VC redistributables.
- Runtime hooks: Registers onnxruntime_runtime_hook.py and torch_runtime_hook.py to resolve DLL paths at runtime.
- Exclusions: Excludes onnx, onnx.reference, paddleocr, and paddle from automatic inclusion to avoid duplication and conflicts.

```mermaid
flowchart TD
Start(["Start Analysis"]) --> Hidden["Apply hiddenimports"]
Hidden --> CollectAll["collect_all for torch, torchvision, ultralytics,<br/>NodeGraphQt, cv2, numpy, paddleocr, onnxruntime"]
CollectAll --> ManualBin["Manually add ONNX Runtime binaries"]
ManualBin --> VCRedist["Copy VC++ redistributables"]
VCRedist --> SandboxAgent["Bundle sandbox_agent.py for RDP"]
SandboxAgent --> Filter["Filter conflicting libiomp5md.dll from paddle libs"]
Filter --> Normalize["Normalize destination paths to avoid _internal/_internal nesting"]
Normalize --> Exclude["Exclude onnx, onnx.reference, paddleocr, paddle"]
Exclude --> BuildExe["Build EXE and COLLECT"]
BuildExe --> End(["Executable Ready"])
```

**Diagram sources**
- [LoOperApp.spec:121-131](file://LoOperApp.spec#L121-L131)
- [LoOperApp.spec:137-200](file://LoOperApp.spec#L137-L200)
- [LoOperApp.spec:381-417](file://LoOperApp.spec#L381-L417)
- [LoOperApp.spec:440-461](file://LoOperApp.spec#L440-L461)
- [LoOperApp.spec:472](file://LoOperApp.spec#L472)
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)

**Section sources**
- [LoOperApp.spec:27-87](file://LoOperApp.spec#L27-L87)
- [LoOperApp.spec:89-131](file://LoOperApp.spec#L89-L131)
- [LoOperApp.spec:137-200](file://LoOperApp.spec#L137-L200)
- [LoOperApp.spec:381-417](file://LoOperApp.spec#L381-L417)
- [LoOperApp.spec:420-515](file://LoOperApp.spec#L420-L515)
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)

### ONNX Runtime Hook: hook-onnxruntime.py
- Hidden imports: Ensures onnxruntime C-API modules are recognized at analysis time.
- Data files: Includes licenses and notices; copies metadata.
- Binaries: Scans the onnxruntime package directory for .dll/.pyd files and bundles them under onnxruntime/capi.

```mermaid
flowchart TD
HookStart["Import hook"] --> Hidden["Set hiddenimports"]
Hidden --> Data["Collect LICENSE*, Privacy.md, ThirdPartyNotices.txt"]
Data --> Meta["Copy metadata"]
Meta --> Scan["Scan onnxruntime.capi for *.dll/*.pyd"]
Scan --> Bundle["Bundle binaries under onnxruntime/capi"]
Bundle --> HookEnd["Hook complete"]
```

**Diagram sources**
- [hook-onnxruntime.py:4-39](file://hook-onnxruntime.py#L4-L39)

**Section sources**
- [hook-onnxruntime.py:4-39](file://hook-onnxruntime.py#L4-L39)

### ONNX Runtime Runtime Hook: onnxruntime_runtime_hook.py
- Environment: Sets YOLO_AUTOINSTALL=false to avoid auto-downloads in frozen environments.
- Path setup: Locates the internal onnxruntime/capi directory and adds it to the DLL search path.
- Pre-loading: Attempts to load provider and core DLLs to prime the loader.

```mermaid
sequenceDiagram
participant Exec as "LoOper.exe"
participant Hook as "onnxruntime_runtime_hook.py"
Exec->>Hook : Initialize at runtime
Hook->>Hook : Determine internal directory (_internal)
Hook->>Hook : Set PATH and add_dll_directory for onnxruntime/capi
Hook->>Hook : Pre-load onnxruntime_providers_shared.dll and onnxruntime.dll
```

**Diagram sources**
- [onnxruntime_runtime_hook.py:7-34](file://onnxruntime_runtime_hook.py#L7-L34)

**Section sources**
- [onnxruntime_runtime_hook.py:7-34](file://onnxruntime_runtime_hook.py#L7-L34)

### Torch Runtime Hook: torch_runtime_hook.py
- Environment: Sets KMP_DUPLICATE_LIB_OK to avoid OpenMP runtime conflicts.
- Path discovery: Builds a list of candidate base directories for DLLs (executable root, _internal, torch/lib, torchvision, paddle libs, onnxruntime).
- Path configuration: Adds discovered paths to Windows DLL search and PATH.
- Pre-loading: Loads VC++ redistributables, OpenMP, core Torch libraries, and ONNX Runtime providers in a specific order to prevent WinError 1114.

```mermaid
flowchart TD
Init["Initialize torch_runtime_hook.py"] --> Env["Set KMP_DUPLICATE_LIB_OK"]
Env --> Paths["Discover candidate bases:<br/>_MEIPASS/_internal, exe_dir/_internal,<br/>torch/lib, torchvision, paddle libs, onnxruntime"]
Paths --> AddPath["Add_dll_directory and PATH entries"]
AddPath --> Prime["Pre-load DLLs in order:<br/>VC++, OpenMP, c10.dll, torch_cpu.dll,<br/>onnxruntime_providers_shared.dll, onnxruntime.dll"]
Prime --> TorchInit["Early import torch"]
TorchInit --> Done["Hook complete"]
```

**Diagram sources**
- [torch_runtime_hook.py:5-118](file://torch_runtime_hook.py#L5-L118)

**Section sources**
- [torch_runtime_hook.py:5-118](file://torch_runtime_hook.py#L5-L118)

### Data Collection Strategies: AI Models, OCR Assets, Resources, and Sandbox Components
- PaddleOCR models: Bundles detection, recognition, and classification models from the user's local .paddleocr directory into the bundle under _internal/paddle_models.
- Application resources: Copies icons, PNG assets, and schedules into both root and _internal locations to ensure availability in frozen builds.
- Computer vision assets: Includes the entire Computer_vision directory to preserve Python modules and resources.
- **Updated** Sandbox agent: Bundles the lightweight sandbox_agent.py file as a loose file in the LoOper directory for RDP session execution, enabling isolated automation without full GUI dependencies.

```mermaid
flowchart TD
StartDC["Start data collection"] --> Paddle["Bundle PaddleOCR models:<br/>det/en_PP-OCRv3_det_infer,<br/>rec/en_PP-OCRv4_rec_infer,<br/>cls/ch_ppocr_mobile_v2.0_cls_infer"]
Paddle --> Res["Bundle LoOper resources:<br/>icons, PNG, schedules.json"]
Res --> CVDir["Include Computer_vision directory"]
CVDir --> Sandbox["Bundle sandbox_agent.py for RDP sessions"]
Sandbox --> EndDC["Data collection complete"]
```

**Diagram sources**
- [LoOperApp.spec:361-379](file://LoOperApp.spec#L361-L379)
- [LoOperApp.spec:341-356](file://LoOperApp.spec#L341-L356)
- [LoOperApp.spec:357-359](file://LoOperApp.spec#L357-L359)
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)

**Section sources**
- [LoOperApp.spec:341-356](file://LoOperApp.spec#L341-L356)
- [LoOperApp.spec:361-379](file://LoOperApp.spec#L361-L379)
- [LoOperApp.spec:357-359](file://LoOperApp.spec#L357-L359)
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)

### Runtime Hooks Implementation: ONNX Runtime and PyTorch Integration
- ONNX Runtime: The hook ensures providers and core DLLs are discoverable and pre-loaded, preventing runtime failures when providers are dynamically loaded.
- PyTorch: The hook sets up the DLL search path and pre-loads critical libraries in a deterministic order to avoid conflicts with PyQt and other runtimes.

```mermaid
sequenceDiagram
participant Exec as "LoOper.exe"
participant TorchHook as "torch_runtime_hook.py"
participant ORTHook as "onnxruntime_runtime_hook.py"
Exec->>TorchHook : Initialize at runtime
TorchHook->>TorchHook : Configure PATH/add_dll_directory
TorchHook->>TorchHook : Pre-load VC++, OpenMP, Torch, ONNX Runtime
Exec->>ORTHook : Initialize at runtime
ORTHook->>ORTHook : Configure PATH/add_dll_directory
ORTHook->>ORTHook : Pre-load providers and core DLLs
```

**Diagram sources**
- [torch_runtime_hook.py:13-118](file://torch_runtime_hook.py#L13-L118)
- [onnxruntime_runtime_hook.py:21-34](file://onnxruntime_runtime_hook.py#L21-L34)

**Section sources**
- [torch_runtime_hook.py:13-118](file://torch_runtime_hook.py#L13-L118)
- [onnxruntime_runtime_hook.py:21-34](file://onnxruntime_runtime_hook.py#L21-L34)

### Binary Collection, Dependency Filtering, and Bundle Optimization
- Binary collection: Uses collect_all for complex packages and manual addition for ONNX Runtime binaries and VC redistributables.
- Dependency filtering: Removes libiomp5md.dll from paddle libs to avoid conflicts with Torch's version.
- Destination normalization: Strips duplicated "_internal" prefixes to prevent nested paths in the final bundle.
- Exclusions: Excludes onnx, onnx.reference, paddleocr, and paddle from automatic inclusion to reduce duplication and potential conflicts.

```mermaid
flowchart TD
BinStart["Binary collection"] --> CollectAll["collect_all for torch, torchvision, ultralytics,<br/>NodeGraphQt, cv2, numpy, paddleocr, onnxruntime"]
CollectAll --> Manual["Manually add ONNX Runtime binaries and VC redistributables"]
Manual --> FilterLib["Filter libiomp5md.dll from paddle libs"]
FilterLib --> SandboxAgent["Bundle sandbox_agent.py for RDP"]
SandboxAgent --> NormalizeDest["Normalize destinations to avoid _internal/_internal"]
NormalizeDest --> ExcludePkgs["Exclude onnx, onnx.reference, paddleocr, paddle"]
ExcludePkgs --> BinEnd["Optimized binary set"]
```

**Diagram sources**
- [LoOperApp.spec:121-131](file://LoOperApp.spec#L121-L131)
- [LoOperApp.spec:137-200](file://LoOperApp.spec#L137-L200)
- [LoOperApp.spec:440-461](file://LoOperApp.spec#L440-L461)
- [LoOperApp.spec:472](file://LoOperApp.spec#L472)
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)

**Section sources**
- [LoOperApp.spec:121-131](file://LoOperApp.spec#L121-L131)
- [LoOperApp.spec:137-200](file://LoOperApp.spec#L137-L200)
- [LoOperApp.spec:440-461](file://LoOperApp.spec#L440-L461)
- [LoOperApp.spec:472](file://LoOperApp.spec#L472)
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)

### Integration with Application Architecture and Runtime Performance
- Entry point: The main entry imports torch early to avoid DLL initialization conflicts with PyQt on Windows.
- API server: Starts an embedded FastAPI/Uvicorn server for AI services, with frozen-mode path handling and logging.
- OCR engine: Detects OCR availability and uses bundled PaddleOCR models when running from a frozen bundle.
- Computer vision: Initializes ONNX Runtime and Torch DLL paths at module import time and pre-loads providers.
- **Updated** Sandbox execution: Provides lightweight automation capabilities for RDP sessions through the bundled sandbox_agent.py, enabling isolated execution without full GUI dependencies.

```mermaid
sequenceDiagram
participant Main as "LoOper/main.py"
participant TorchHook as "torch_runtime_hook.py"
participant ORTE as "onnxruntime_runtime_hook.py"
participant OCR as "LoOper/player/ocr_engine.py"
participant CV as "LoOper/player/computer_vision.py"
participant Sandbox as "sandbox_agent.py"
Main->>Main : Import torch early to avoid conflicts
Main->>Main : Start API server in frozen mode
Main->>OCR : Initialize OCR engine (bundled models if frozen)
Main->>CV : Import computer vision (sets up DLL paths)
CV->>ORTE : Ensure ONNX Runtime DLLs are available
CV->>TorchHook : Ensure Torch DLLs are available
Sandbox->>Sandbox : Start HTTP server for RDP automation
```

**Diagram sources**
- [LoOper/main.py:106-110](file://LoOper/main.py#L106-L110)
- [LoOper/main.py:171-353](file://LoOper/main.py#L171-L353)
- [LoOper/player/ocr_engine.py:58-91](file://LoOper/player/ocr_engine.py#L58-L91)
- [LoOper/player/computer_vision.py:8-63](file://LoOper/player/computer_vision.py#L8-L63)
- [LoOper/sandbox_agent.py:106-157](file://LoOper/sandbox_agent.py#L106-L157)

**Section sources**
- [LoOper/main.py:106-110](file://LoOper/main.py#L106-L110)
- [LoOper/main.py:171-353](file://LoOper/main.py#L171-L353)
- [LoOper/player/ocr_engine.py:58-91](file://LoOper/player/ocr_engine.py#L58-L91)
- [LoOper/player/computer_vision.py:8-63](file://LoOper/player/computer_vision.py#L8-L63)
- [LoOper/sandbox_agent.py:106-157](file://LoOper/sandbox_agent.py#L106-L157)

## Sandbox Execution Integration

### Enhanced LoOperApp.spec Configuration for Sandbox Distribution
The build configuration now includes explicit bundling of the sandbox_agent.py file to ensure proper distribution of required components for sandbox execution in RDP sessions. This enhancement enables the creation of lightweight automation agents that can operate independently of the full LoOper GUI interface.

**Updated** The sandbox agent is bundled as a loose file in the LoOper directory structure, allowing it to be executed via scheduled tasks without requiring the complete application bundle. This approach provides security isolation while maintaining access to necessary dependencies.

```mermaid
flowchart TD
SandboxStart["Sandbox Agent Configuration"] --> Bundle["Bundle sandbox_agent.py in LoOper directory"]
Bundle --> RDP["RDP Session Execution"]
RDP --> Isolation["Security Isolation"]
Isolation --> Automation["Remote Automation"]
Automation --> Status["Status Reporting"]
Status --> Cleanup["Resource Cleanup"]
```

**Diagram sources**
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)
- [LoOper/sandbox_agent.py:1-157](file://LoOper/sandbox_agent.py#L1-L157)

**Section sources**
- [LoOperApp.spec:357-361](file://LoOperApp.spec#L357-L361)
- [LoOper/sandbox_agent.py:1-157](file://LoOper/sandbox_agent.py#L1-L157)

### Sandbox Agent Architecture and Capabilities
The sandbox_agent.py provides a lightweight HTTP server designed for RDP session automation. It operates with minimal dependencies and focuses on essential automation actions including mouse control, keyboard input, screen capture, and basic interaction primitives.

Key features:
- HTTP-based API for remote command execution
- Secure action execution with fail-safe mechanisms
- Minimal resource footprint for RDP environments
- Comprehensive error handling and logging
- Flexible port configuration via environment variables or command line arguments

**Section sources**
- [LoOper/sandbox_agent.py:1-157](file://LoOper/sandbox_agent.py#L1-L157)

## Dependency Analysis
The build depends on:
- PyInstaller 6+ with dedicated contents directory handling
- ONNX Runtime and PyTorch for AI/ML inference
- Ultralytics for YOLO-based computer vision
- PaddleOCR for OCR tasks
- OpenCV for image processing
- **Updated** PyAutoGUI for RDP automation capabilities

```mermaid
graph TB
Spec["LoOperApp.spec"] --> PyInstaller["PyInstaller 6+"]
Spec --> ONNX["onnxruntime"]
Spec --> Torch["torch + torchvision"]
Spec --> UTY["ultralytics"]
Spec --> Paddle["paddleocr + paddle"]
Spec --> CV["opencv-python"]
Spec --> Qt["PyQt5 + NodeGraphQt"]
Spec --> Sandbox["PyAutoGUI for sandbox automation"]
```

**Diagram sources**
- [requirements.txt:89-127](file://requirements.txt#L89-L127)
- [requirements.txt:171-172](file://requirements.txt#L171-L172)
- [requirements.txt:182](file://requirements.txt#L182)
- [requirements.txt:88](file://requirements.txt#L88)
- [requirements.txt:99](file://requirements.txt#L99)
- [requirements.txt:108](file://requirements.txt#L108)

**Section sources**
- [requirements.txt:89-127](file://requirements.txt#L89-L127)
- [requirements.txt:171-172](file://requirements.txt#L171-L172)
- [requirements.txt:182](file://requirements.txt#L182)
- [requirements.txt:88](file://requirements.txt#L88)
- [requirements.txt:99](file://requirements.txt#L99)
- [requirements.txt:108](file://requirements.txt#L108)

## Performance Considerations
- Pre-loading critical DLLs reduces runtime initialization overhead and avoids loader conflicts.
- Bundling models and assets minimizes startup latency by avoiding network downloads.
- Normalizing destinations avoids redundant nested paths and keeps the bundle compact.
- Excluding unnecessary packages reduces bundle size and potential import conflicts.
- **Updated** Sandbox agent provides lightweight automation for RDP sessions, reducing memory footprint compared to full GUI execution.

## Troubleshooting Guide
Common issues and resolutions:
- Missing dependencies at runtime:
  - Ensure all dynamic imports are covered by hiddenimports in the spec.
  - Verify that collect_all is applied to complex packages and that binaries are explicitly added for ONNX Runtime.
- DLL conflicts (e.g., WinError 1114):
  - Use the Torch runtime hook to pre-load VC++ redistributables, OpenMP, and core Torch/ONNX Runtime DLLs in a deterministic order.
  - Filter conflicting libiomp5md.dll from paddle libs to avoid version mismatches.
- Missing ONNX Runtime providers:
  - Confirm onnxruntime_runtime_hook registers and pre-loads providers and core DLLs.
- OCR model not found in frozen builds:
  - Ensure PaddleOCR models are bundled into _internal/paddle_models and that the OCR engine detects them at runtime.
- **Updated** Sandbox agent not responding in RDP:
  - Verify sandbox_agent.py is properly bundled in the LoOper directory.
  - Check that the HTTP server starts successfully and listens on the expected port.
  - Ensure firewall exceptions are configured for the agent's port.
  - Verify RDP session has proper permissions for automation actions.

**Section sources**
- [LoOperApp.spec:440-461](file://LoOperApp.spec#L440-L461)
- [torch_runtime_hook.py:75-118](file://torch_runtime_hook.py#L75-L118)
- [onnxruntime_runtime_hook.py:21-34](file://onnxruntime_runtime_hook.py#L21-L34)
- [LoOper/player/ocr_engine.py:58-91](file://LoOper/player/ocr_engine.py#L58-L91)
- [LoOper/sandbox_agent.py:106-157](file://LoOper/sandbox_agent.py#L106-L157)

## Conclusion
The LoOperV2 PyInstaller configuration is designed to reliably package complex AI/ML dependencies across platforms, with enhanced support for sandbox execution in RDP environments. By combining hidden imports, targeted data and binary collection, robust runtime hooks, and the bundled sandbox_agent.py file, the build ensures that ONNX Runtime and PyTorch DLLs are discoverable and pre-loaded, while OCR and computer vision assets are available in frozen environments. The sandbox agent provides secure, isolated automation capabilities for remote desktop sessions without requiring the full GUI interface. Developers can customize the spec to adjust bundle size and asset inclusion, guided by the strategies outlined here, while leveraging the enhanced sandbox execution capabilities for production deployments.