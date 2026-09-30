"""data_lineage_tracker — lightweight dataset provenance tracking.

Register immutable dataset versions (content hash, row/column counts,
schema fingerprint, source URI), record transformation steps as a
parent -> child lineage graph, verify files on disk against the recorded
hashes, and answer "what produced this dataset?" Export the graph as JSON
or GraphViz DOT.
"""

from .models import DatasetVersion, Transform
from .report import LineageReport, lineage_report
from .store import LineageError, LineageStore
from .verify import VerifyResult, verify_chain, verify_dataset

__all__ = [
    "DatasetVersion",
    "Transform",
    "LineageError",
    "LineageReport",
    "LineageStore",
    "VerifyResult",
    "lineage_report",
    "verify_chain",
    "verify_dataset",
]
__version__ = "0.1.0"
