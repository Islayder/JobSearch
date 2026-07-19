"""Collection policy helpers shared by CLI and collectors."""

BOARD_SNAPSHOT_COLLECTORS = {"greenhouse", "lever"}


def supports_closure_snapshot(collector: str) -> bool:
    return collector in BOARD_SNAPSHOT_COLLECTORS
