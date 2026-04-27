"""Supply Chain Risk Intelligence backend package.

The project is used in two ways:
- `uvicorn app.main:app` from inside `backend/`
- `import backend.app...` from tests run at the repository root

Expose both package names so mixed imports resolve to the same module tree.
"""

from __future__ import annotations

import os
import sys
import types

_app_module = sys.modules[__name__]
_backend_dir = os.path.dirname(os.path.dirname(__file__))

sys.modules.setdefault("app", _app_module)

if "backend" not in sys.modules:
    backend_pkg = types.ModuleType("backend")
    backend_pkg.__path__ = [_backend_dir]
    sys.modules["backend"] = backend_pkg

sys.modules.setdefault("backend.app", _app_module)
