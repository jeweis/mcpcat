"""仅绑定 loopback 的 Inspector 集成夹具，复用隔离资源数据。"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app_flutter_fixture import create_fixture
from inspector_workbench_fixture import attach_workbench
import uvicorn

if __name__ == "__main__":
    with TemporaryDirectory(prefix="mcpcat-inspector-ui-") as directory:
        app = create_fixture(Path(directory))
        attach_workbench(app)
        app.state.port = 18766
        uvicorn.run(app, host="127.0.0.1", port=18766)
