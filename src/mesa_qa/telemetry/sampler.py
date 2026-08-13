from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Any, Optional
import psutil
import logging

logger = logging.getLogger("mesa_qa.telemetry_sampler")


class ResourceSampler:
    def __init__(self, run_dir: Path, warn_rss_mb: float = 6000.0, hard_stop_rss_mb: float = 12000.0):
        self.run_dir = run_dir.resolve()
        self.warn_rss_mb = warn_rss_mb
        self.hard_stop_rss_mb = hard_stop_rss_mb
        self.log_file = self.run_dir / "logs" / "resources.jsonl"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def sample_process(self, pid: Optional[int]) -> Dict[str, Any]:
        timestamp = time.time()
        metrics: Dict[str, Any] = {
            "timestamp": timestamp,
            "pid": pid,
            "rss_mb": 0.0,
            "cpu_percent": 0.0,
            "num_threads": 0,
            "status": "not_running",
        }

        if pid:
            try:
                proc = psutil.Process(pid)
                mem = proc.memory_info()
                metrics["rss_mb"] = round(mem.rss / (1024 * 1024), 2)
                metrics["cpu_percent"] = proc.cpu_percent(interval=None)
                metrics["num_threads"] = proc.num_threads()
                metrics["status"] = proc.status()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                metrics["status"] = "dead"

        # Log sample to jsonl file
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics) + "\n")

        if metrics["rss_mb"] > self.hard_stop_rss_mb:
            logger.error("HARD STOP: RSS memory %s MB exceeded hard stop limit of %s MB!", metrics["rss_mb"], self.hard_stop_rss_mb)

        elif metrics["rss_mb"] > self.warn_rss_mb:
            logger.warning("WARNING: RSS memory %s MB exceeded warning threshold of %s MB", metrics["rss_mb"], self.warn_rss_mb)

        return metrics
