"""Offline CRAG tooling: dataset building, evaluator training, inference, eval.

Nothing here is imported by the app at runtime; these are scripts behind
``python -m AI.crag.offline.<name>`` (or the argparse ``main()`` of the
corresponding upstream script).  They are the torch-side half of the port:

===============  =====================================================
upstream         port
===============  =====================================================
data_process.py  offline/dataset.py
train_evaluator  offline/train_evaluator.py
CRAG_Inference   offline/inference.py
eval.py          offline/eval.py
metrics.py       offline/metrics.py
-                offline/calibrate.py (threshold sweep for this app's
                 evaluator backend)
===============  =====================================================

The runtime evaluator is Laya (see ``AI/crag/evaluator.py``), so training a
T5 evaluator is only needed when a dataset-level reproduction is wanted; the
calibration script is what tunes the Laya thresholds for a given corpus.
"""
