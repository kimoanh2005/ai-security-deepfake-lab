import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class SecurityService:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or os.getenv("SECURITY_DB_PATH") or Path(__file__).resolve().parents[2] / "storage" / "security.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.rate_limit_records = {}
        self.rate_limit_max_requests = int(os.getenv("SANDBOX_MAX_REQUESTS", "5"))
        self.rate_limit_window_seconds = int(os.getenv("SANDBOX_WINDOW_SECONDS", "30"))
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, timestamp TEXT, kind TEXT, data TEXT)")

            db.execute("CREATE TABLE IF NOT EXISTS security_events (id TEXT PRIMARY KEY, timestamp TEXT, data TEXT)")
            db.execute("INSERT OR IGNORE INTO security_events SELECT id,timestamp,data FROM events WHERE kind='log'")

    def connect(self):
        return sqlite3.connect(self.db_path, timeout=15)

    def save(self, kind, data):
        entry = {**data, "id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat()}
        with self.connect() as db:
            db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (entry["id"], entry["timestamp"], kind, json.dumps(entry, ensure_ascii=False)))
        return entry

    def list_events(self, kind, limit=200):
        with self.connect() as db:
            if kind == "log":
                return [json.loads(row[0]) for row in db.execute("SELECT data FROM security_events ORDER BY timestamp DESC LIMIT ?", (limit,))]
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM events WHERE kind=? ORDER BY timestamp DESC LIMIT ?", (kind, limit))]

    @property
    def security_logs(self):
        return self.list_events("log")

    def clear_logs(self):
        with self.connect() as db:
            db.execute("DELETE FROM events WHERE kind='log'")
            db.execute("DELETE FROM security_events")

    def inspect_prompt(self, prompt):
        text = "".join(c for c in unicodedata.normalize("NFD", prompt.lower()) if unicodedata.category(c) != "Mn").replace("đ", "d")
        rules = [
            ("Instruction Override", r"(bo qua|hay quen|quen het|ignore|disregard|forget)[\s\S]{0,160}(chi dan|huong dan|quy dinh|instructions|rules|system)"),
            ("Role-play / Jailbreak", r"(emergency\s+debug|debug\s+mode|che do\s+(debug|quan tri|khan cap)|dan mode|jailbreak)"),
            ("Secret Extraction", r"(in ra|tiet lo|cho toi xem|reveal|show|print|display)[\s\S]{0,160}(bi mat|mat khau|flag|secret|token|system prompt)"),
            ("Delimiter Spoofing", r"(<\/?system>|\[system\])"),
        ]
        matches = [name for name, pattern in rules if re.search(pattern, text)]
        return bool(matches), matches[0] if matches else None, matches

    def inspect_output(self, text):
        pattern = r"FLAG\s*\{[^}]*\}?|AI_SECURITY_OLLAMA_2026"
        leaked = bool(re.search(pattern, text, re.I))
        return leaked, re.sub(pattern, "[ĐÃ CHẶN BÍ MẬT GIẢ LẬP]", text, flags=re.I)

    def log_event(self, event_type, client_ip, prompt="", is_attack=False, defense_applied=False, action_taken="", details="", **extra):
        _, safe_prompt = self.inspect_output(prompt)
        data = dict(event_type=event_type, client_ip=client_ip, prompt_sample=safe_prompt[:500], is_attack=is_attack, defense_applied=defense_applied, action_taken=action_taken, details=details, **extra)
        entry = {**data, "id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat()}
        with self.connect() as db:
            db.execute("INSERT INTO security_events VALUES (?,?,?)", (entry["id"], entry["timestamp"], json.dumps(entry, ensure_ascii=False)))
        return entry

    def check_rate_limit(self, client_ip):
        with self.lock:
            now = time.monotonic()
            self.rate_limit_records = {key: values for key, values in self.rate_limit_records.items() if values and values[-1] > now - self.rate_limit_window_seconds}
            timestamps = [t for t in self.rate_limit_records.get(client_ip, []) if t > now - self.rate_limit_window_seconds]
            if len(timestamps) >= self.rate_limit_max_requests:
                return False, 0, max(1, int(self.rate_limit_window_seconds - (now - timestamps[0])) + 1)
            timestamps.append(now)
            self.rate_limit_records[client_ip] = timestamps
            return True, self.rate_limit_max_requests - len(timestamps), max(1, int(self.rate_limit_window_seconds - (now - timestamps[0])) + 1)


security_service = SecurityService()
