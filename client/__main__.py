import sys

if sys.argv[1:2] == ["hook"]:
    from .hooks import main as hook_main

    raise SystemExit(hook_main(sys.argv[2:]))

from .cli import main

raise SystemExit(main())
