"""Sample trade documents with known correct answers.

Run from the repo root with the backend venv's Python:

    backend\\.venv\\Scripts\\python -m samples.generate

Modules, one job each:
- shipments.py  the ground truth: what every invoice says, and which errors are planted
- render.py     draws a commercial invoice as a text PDF (fpdf2)
- degrade.py    turns the text PDF into a scan-like image (Pillow, OpenCV), fixed seeds
- answers.py    the answer-file schema and how an answer file is built from the ground truth
- generate.py   writes every document and answer file (the one command)
"""
