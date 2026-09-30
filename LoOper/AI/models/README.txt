Place your llama.cpp GGUF model files (.gguf) and vision projector files (.gguf) in this directory.

Needle 2 dev-tool engine (Code Node Studio): needle2/needle.exe (Windows x86-64, Apache-2.0, HF Cactus-Compute/needle2). Self-contained; bundled by build_pipeline.

Laya System-1 decision engine: laya-GGUF/ ships with the app
(laya_english_ud_q4_k_m.gguf / q8_0 / f16; engine binary at ../bin/laya.exe).
Used by the Orchestrator picker, the Input decision evaluator, and the
ComoRAG / form-filler verdicts.  LOOPER_LAYA=off forces every consumer back
to its LLM path; LOOPER_LAYA_MODEL overrides the quant.
