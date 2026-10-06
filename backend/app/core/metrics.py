"""Low-cardinality process HTTP metrics. Never label with user IDs, paths or source text."""

from threading import Lock

BUCKETS = (0.1, 0.5, 1.0, 5.0, 30.0, 120.0)


class HttpMetrics:
    def __init__(self) -> None:
        self.lock = Lock()
        self.rows: dict[tuple[str, str], tuple[list[int], float, int]] = {}

    def observe(self, route: str, status: int, seconds: float) -> None:
        with self.lock:
            counts, total, count = self.rows.get(
                (route, str(status // 100) + "xx"), ([0] * len(BUCKETS), 0.0, 0)
            )
            self.rows[route, str(status // 100) + "xx"] = (
                [n + int(seconds <= edge) for n, edge in zip(counts, BUCKETS, strict=True)],
                total + seconds,
                count + 1,
            )

    def render(self) -> str:
        lines = ["# TYPE repopilot_http_seconds histogram"]
        with self.lock:
            for (route, status), (counts, total, count) in sorted(self.rows.items()):
                labels = f'route="{route}",status="{status}"'
                for edge, n in zip(BUCKETS, counts, strict=True):
                    lines.append(f'repopilot_http_seconds_bucket{{{labels},le="{edge}"}} {n}')
                lines.extend(
                    [
                        f'repopilot_http_seconds_bucket{{{labels},le="+Inf"}} {count}',
                        f"repopilot_http_seconds_sum{{{labels}}} {total}",
                        f"repopilot_http_seconds_count{{{labels}}} {count}",
                    ]
                )
        return "\n".join(lines) + "\n"
