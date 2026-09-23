from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jevmesh.dataset import prepare_event_questions
prepare_event_questions()
