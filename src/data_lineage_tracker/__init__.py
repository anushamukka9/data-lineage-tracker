"""data_lineage_tracker — lightweight dataset provenance tracking.

Register immutable dataset versions (content hash, row/column counts,
schema fingerprint, source URI), record transformation steps as a
parent -> child lineage graph, and answer "what produced this dataset?"
Export the graph as JSON or GraphViz DOT.
"""

from .models import DatasetVersion, Transform
from .store import LineageStore

__all__ = ["DatasetVersion", "Transform", "LineageStore"]
__version__ = "0.1.0"
