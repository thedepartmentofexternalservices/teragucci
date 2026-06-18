"""
Machine pool management — tracks Flame workstations, health, and assignments.

Maintains a registry of available machines, probes their health,
and assigns users to machines based on user assignments or floating pool.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

HEALTH_INTERVAL = 15.0     # seconds between health probes
HEALTH_FAIL_THRESHOLD = 3  # consecutive failures to mark unhealthy
HEALTH_OK_THRESHOLD = 2    # consecutive successes to mark healthy


@dataclass
class Machine:
    """A Teraguchi server in the pool."""
    name: str = ""
    host: str = ""
    port: int = 4443
    gpu: str = ""
    priority: int = 10            # Lower = preferred
    tags: list = field(default_factory=list)

    # Legacy fields (ignored, kept for backward compat with old configs)
    pool: str = ""
    assigned_user: str = ""

    # Runtime state (not from config)
    healthy: bool = False
    active_sessions: list = field(default_factory=list)
    last_probe: float = 0.0
    consecutive_ok: int = 0
    consecutive_fail: int = 0
    load_avg: float = 0.0
    uptime_s: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "gpu": self.gpu,
            "priority": self.priority,
            "tags": self.tags,
            "healthy": self.healthy,
            "active_sessions": self.active_sessions,
            "load_avg": self.load_avg,
        }


class MachinePool:
    """Manages the fleet of Teraguchi servers."""

    def __init__(self, machines_config: list[dict],
                 assignments: Optional[dict] = None):
        self._machines: dict[str, Machine] = {}
        for cfg in machines_config:
            m = Machine(**{k: v for k, v in cfg.items()
                          if k in Machine.__dataclass_fields__})
            self._machines[m.name] = m
        self._probe_task: Optional[asyncio.Task] = None

        # SEC-01 dev escape hatch. Double-gated with TERAGUCHI_ACCEPT_INSECURE=1.
        self._insecure_skip_verify: bool = False
        self._ca_bundle: Optional[str] = None

        # Build assignment lookups
        self._user_machines: dict[str, set[str]] = {}   # user → allowed machine names
        self._machine_users: dict[str, set[str]] = {}   # machine → assigned users

        effective = assignments or {}

        # Legacy support: if no assignments, build from pool/assigned_user fields
        if not effective:
            for cfg in machines_config:
                if cfg.get("pool") == "dedicated" and cfg.get("assigned_user"):
                    user = cfg["assigned_user"]
                    effective.setdefault(user, []).append(cfg["name"])
            if effective:
                logger.info("Converted legacy dedicated assignments: %s", effective)

        for user, machine_names in effective.items():
            valid = set()
            for name in machine_names:
                if name not in self._machines:
                    logger.warning("Assignment references unknown machine: %s", name)
                    continue
                valid.add(name)
                self._machine_users.setdefault(name, set()).add(user)
            if valid:
                self._user_machines[user] = valid

        # Floating pool = machines NOT referenced in any assignment
        self._floating: set[str] = {
            name for name in self._machines
            if name not in self._machine_users
        }

        logger.info("Pool: %d machines, %d assigned users, %d floating",
                     len(self._machines), len(self._user_machines), len(self._floating))
        for user, names in self._user_machines.items():
            logger.info("  %s → %s", user, sorted(names))
        if self._floating:
            logger.info("  floating → %s", sorted(self._floating))

    @property
    def machines(self) -> dict[str, Machine]:
        return self._machines

    def user_can_access(self, username: str, machine_name: str) -> bool:
        """Check if a user is allowed to access a specific machine."""
        if username in self._user_machines:
            return machine_name in self._user_machines[username]
        return machine_name in self._floating

    def start_health_probes(self):
        """Start background health check loop."""
        if self._probe_task is None:
            self._probe_task = asyncio.ensure_future(self._probe_loop())

    def stop(self):
        if self._probe_task:
            self._probe_task.cancel()
            self._probe_task = None

    async def _probe_loop(self):
        """Periodically probe all machines."""
        while True:
            try:
                await self._probe_all()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Probe loop error: %s", e)
            await asyncio.sleep(HEALTH_INTERVAL)

    async def _probe_all(self):
        """Probe all machines concurrently."""
        tasks = [self._probe_one(m) for m in self._machines.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe_one(self, machine: Machine):
        """Probe a single machine's /status endpoint."""
        url = f"https://{machine.host}:{machine.port}/status"
        from common.tls_opt_out import build_client_ssl_context
        ssl_ctx = build_client_ssl_context(
            insecure_cli_flag=getattr(self, "_insecure_skip_verify", False),
            ca_bundle=getattr(self, "_ca_bundle", None),
            site_label="broker_probe",
        )
        try:
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_ctx)
            ) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        machine.active_sessions = data.get("active_sessions", [])
                        machine.load_avg = data.get("load_avg", [0])[0] if data.get("load_avg") else 0
                        machine.uptime_s = data.get("uptime_s", 0)
                        machine.consecutive_ok += 1
                        machine.consecutive_fail = 0
                        if machine.consecutive_ok >= HEALTH_OK_THRESHOLD:
                            if not machine.healthy:
                                logger.info("Machine %s is now healthy", machine.name)
                            machine.healthy = True
                    else:
                        raise Exception(f"HTTP {resp.status}")
        except Exception as e:
            machine.consecutive_fail += 1
            machine.consecutive_ok = 0
            if machine.consecutive_fail >= HEALTH_FAIL_THRESHOLD:
                if machine.healthy:
                    logger.warning("Machine %s is now unhealthy: %s", machine.name, e)
                machine.healthy = False
        machine.last_probe = time.time()

    def assign(self, username: str, groups: list[str]) -> Optional[Machine]:
        """
        Assign a machine to a user.

        Priority:
        1. Existing active session on an allowed machine (reconnect)
        2. Available machine from user's assigned set (or floating pool)
        3. Least-loaded machine from allowed set (if all busy)
        """
        is_admin = "teraguchi-admins" in groups

        # Determine candidate machines for this user
        if username in self._user_machines:
            candidate_names = self._user_machines[username]
            pool_type = "assigned"
        else:
            candidate_names = self._floating
            pool_type = "floating"

        # 1. Reconnect — existing session on an allowed machine
        for name in candidate_names:
            m = self._machines[name]
            if m.healthy and username in m.active_sessions:
                logger.info("Reconnect: %s → %s", username, m.name)
                return m

        # 2. Find available machine (no active sessions)
        available = [
            self._machines[name] for name in candidate_names
            if self._machines[name].healthy
            and len(self._machines[name].active_sessions) == 0
        ]

        if not available:
            # Allow sharing: assigned users can share their pool,
            # admins can share any pool
            if is_admin or username in self._user_machines:
                available = [
                    self._machines[name] for name in candidate_names
                    if self._machines[name].healthy
                ]
            if not available:
                logger.warning("No machines available for %s (%s pool)", username, pool_type)
                return None

        # Sort: fewest sessions first, then by priority (lower = better)
        available.sort(key=lambda m: (len(m.active_sessions), m.priority))
        chosen = available[0]
        logger.info("Assigned %s: %s → %s (priority=%d)",
                    pool_type, username, chosen.name, chosen.priority)
        return chosen

    def get_status(self) -> list[dict]:
        """Get status of all machines with pool info."""
        result = []
        for m in self._machines.values():
            d = m.to_dict()
            d["pool"] = "floating" if m.name in self._floating else "assigned"
            d["assigned_users"] = sorted(self._machine_users.get(m.name, set()))
            result.append(d)
        return result

    def update_assignments(self, assignments: Optional[dict] = None):
        """Rebuild assignment lookups from a new assignments dict (live reload)."""
        self._user_machines.clear()
        self._machine_users.clear()

        effective = assignments or {}
        for user, machine_names in effective.items():
            valid = set()
            for name in machine_names:
                if name not in self._machines:
                    logger.warning("Assignment references unknown machine: %s", name)
                    continue
                valid.add(name)
                self._machine_users.setdefault(name, set()).add(user)
            if valid:
                self._user_machines[user] = valid

        self._floating = {
            name for name in self._machines
            if name not in self._machine_users
        }

        logger.info("Assignments reloaded: %d assigned users, %d floating",
                     len(self._user_machines), len(self._floating))

    def release(self, username: str, machine_name: str):
        """Release a machine assignment (for session tracking)."""
        m = self._machines.get(machine_name)
        if m and username in m.active_sessions:
            m.active_sessions.remove(username)
            logger.info("Released: %s from %s", username, machine_name)
