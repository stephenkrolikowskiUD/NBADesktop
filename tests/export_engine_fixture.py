import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_nba_readiness import EngineTests

def output(games):
    book,day=EngineTests().run_engine(games=games,ai=games)
    return {title:sheet.get_all_records() for title,sheet in book.tabs.items()}

Path(sys.argv[1]).write_text(json.dumps({'ready':output(True),'offDay':output(False)},default=str))
