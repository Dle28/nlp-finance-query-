from pathlib import Path
import pytest
from finance_query.grounding_repairs_v2 import materialize
def test_nonblank_decision_fails_closed(tmp_path:Path):
 q=tmp_path/"q.jsonl";q.write_text('{"decision_contract":{"decision":"accept_repair"}}\n')
 with pytest.raises(ValueError,match="NON_EMPTY_REPAIR_OVERLAY_UNSUPPORTED"):materialize([q],tmp_path/"out")
