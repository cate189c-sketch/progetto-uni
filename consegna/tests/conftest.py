import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.getLogger("kine.config").setLevel(logging.ERROR)

# I test che leggono un file di configurazione devono trovarlo anche quando
# pytest parte dalla radice del repository invece che da consegna/.
CONSEGNA = Path(__file__).resolve().parents[1]
