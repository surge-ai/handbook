"""Legacy ``syntara`` setup hook — forwards to ``core.setup``.

The setup step (copying uploaded context files into the workdir) is identical
for the shim, so we delegate to core's implementation and just log that the
legacy entrypoint was used.

REMOVE with the rest of the ``syntara`` package after 2026-06-18 (see
``syntara._compat``).
"""

from core.setup import main as _core_setup_main
from syntara._compat import _log_forward


def main() -> None:
    _log_forward("setup")
    _core_setup_main()


if __name__ == "__main__":
    main()
