"""Build entrypoint kept above ``ax_workspace.entrypoints`` to avoid stdlib name shadowing."""
from ax_workspace.entrypoints.protected import main


if __name__ == "__main__":
    raise SystemExit(main())
