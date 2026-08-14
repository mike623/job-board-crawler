"""Tell a broken scan apart from a search that genuinely matched nothing.

A crawl can report success with a completely empty body — observed live: status 200,
success True, zero bytes of markdown and HTML, zero jobs. An immediate retry of the same
search returned 25 results.

Left unclassified that run is indistinguishable, in the output and in the cron log, from a
search with no matches, so a board can stop producing data for days unnoticed.
"""
from __future__ import annotations

OK = "ok"
EMPTY = "empty-body"
FAILED = "failed"

# A page that renders almost nothing is not a page. Real search results, including genuinely
# empty ones, carry navigation, headers and footers well beyond this.
MIN_BODY_CHARS = 200


def classify(result) -> str:
    """Classify one crawl result as ok, an empty body, or an outright failure."""
    if not getattr(result, "success", False):
        return FAILED
    body = max(len(str(result.markdown or "").strip()), len((result.html or "").strip()))
    return EMPTY if body < MIN_BODY_CHARS else OK


class RunHealth:
    """Tally per-search outcomes so the scan can report and exit honestly."""

    def __init__(self, board: str):
        self.board = board
        self.outcomes: list[str] = []

    def record(self, result) -> str:
        outcome = classify(result)
        self.outcomes.append(outcome)
        return outcome

    @property
    def searches(self) -> int:
        return len(self.outcomes)

    @property
    def usable(self) -> int:
        return sum(1 for o in self.outcomes if o == OK)

    @property
    def empty(self) -> int:
        return sum(1 for o in self.outcomes if o == EMPTY)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o == FAILED)

    @property
    def all_broken(self) -> bool:
        """Every search failed or came back empty — the run produced nothing usable."""
        return self.searches > 0 and self.usable == 0

    def report(self) -> str:
        parts = [f"{self.usable}/{self.searches} searches returned a usable page"]
        if self.empty:
            parts.append(f"{self.empty} empty")
        if self.failed:
            parts.append(f"{self.failed} failed")
        return f"{self.board}: " + ", ".join(parts)

    def finish(self) -> None:
        """Print the health line, and exit non-zero if nothing usable came back."""
        print(self.report())
        if self.all_broken:
            raise SystemExit(
                f"{self.board}: no search returned a usable page. Treating this as a failed run "
                f"rather than an empty result set — check the raw captures."
            )
