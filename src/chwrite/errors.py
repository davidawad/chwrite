"""Shared exception type for chwrite."""


class ChwriteError(Exception):
    """A user-facing chwrite error.

    Args:
        message: Human-readable description, written to stderr by the CLI
            entrypoint.
        code: Process exit status. Follows the exit-status convention
            documented in SPEC.md section 17 (0 valid, 1 violation, 2
            config/runtime error) unless a command defines its own
            narrower contract (e.g. check-path's --claude-hook mode).
    """

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code
