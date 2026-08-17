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

    def sample_process_tree(self, pid: Optional[int]) -> Dict[str, Any]:
        """Sample RSS memory, CPU%, threads, and child process count recursively across process tree."""
        timestamp = time.time()
        metrics: Dict[str, Any] = {
            "timestamp": timestamp,
            "pid": pid,
            "rss_mb": 0.0,
            "cpu_percent": 0.0,
            "num_threads": 0,
            "num_processes": 0,
            "status": "not_running",
            "hard_limit_exceeded": False,
        }

        if pid:
            try:
                root_proc = psutil.Process(pid)
                procs = [root_proc]
                try:
                    procs.extend(root_proc.children(recursive=True))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

                total_rss = 0
                total_cpu = 0.0
                total_threads = 0
                active_count = 0

                for proc in procs:
                    try:
                        mem = proc.memory_info()
                        total_rss += mem.rss
                        total_cpu += proc.cpu_percent(interval=None)
                        total_threads += proc.num_threads()
                        active_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue

                if active_count > 0:
                    metrics["rss_mb"] = round(total_rss / (1024 * 1024), 2)
                    metrics["cpu_percent"] = round(total_cpu, 2)
                    metrics["num_threads"] = total_threads
                    metrics["num_processes"] = active_count
                    metrics["status"] = root_proc.status()
                else:
                    metrics["status"] = "dead"

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                metrics["status"] = "dead"

        if metrics["rss_mb"] > self.hard_stop_rss_mb:
            metrics["hard_limit_exceeded"] = True
            logger.error(
                "HARD STOP: Process-tree RSS memory %s MB exceeded hard stop limit of %s MB!",
                metrics["rss_mb"],
                self.hard_stop_rss_mb,
            )
        elif metrics["rss_mb"] > self.warn_rss_mb:
            logger.warning(
                "WARNING: Process-tree RSS memory %s MB exceeded warning threshold of %s MB",
                metrics["rss_mb"],
                self.warn_rss_mb,
            )

        # Log sample to jsonl file
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(metrics) + "\n")
        except Exception as exc:
            logger.error("Failed to write resource sample to log: %s", exc)

        return metrics

    def sample_process(self, pid: Optional[int]) -> Dict[str, Any]:
        """Backward compatible alias for sample_process_tree."""
        return self.sample_process_tree(pid)
