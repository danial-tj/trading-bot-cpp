"""Bounded in-memory Questrade download jobs.

Jobs and request-id deduplication survive only this process and retain the latest
20 jobs. Credentials stay solely inside the provider client; this module persists
only a fully downloaded, validated dataset through Store.import_dataset.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
from datetime import date
import re
import threading
import uuid

from .store import Conflict, NotFound, canonical


TERMINAL = {"completed", "failed", "cancelled"}
IMPORT_KEYS = {"request_id", "symbol_id", "start_date", "end_date", "bar_minutes"}
SUPPORTED_MINUTES = {0, 1, 2, 5, 10, 15, 30}


def exact_object(payload, keys, label):
    if not isinstance(payload, dict) or set(payload) != set(keys):
        raise ValueError(f"{label} requires exactly the documented fields")
    return payload


def _validate_request(payload):
    exact_object(payload, IMPORT_KEYS, "Questrade historical import")
    request_id = payload["request_id"]
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
        raise ValueError("request_id must contain 1-128 letters, digits, or ._:- characters")
    if type(payload["symbol_id"]) is not int or not 1 <= payload["symbol_id"] <= 2147483647:
        raise ValueError("symbol_id must be an integer from 1 to 2147483647")
    if type(payload["bar_minutes"]) is not int or payload["bar_minutes"] not in SUPPORTED_MINUTES:
        raise ValueError("bar_minutes must be 0, 1, 2, 5, 10, 15 or 30")
    dates = []
    for key in ("start_date", "end_date"):
        value = payload[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError(f"{key} must use YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{key} must be a valid Gregorian date") from None
        if parsed.year < 1900:
            raise ValueError(f"{key} must be in or after 1900")
        dates.append(parsed)
    if dates[0] > dates[1] or (dates[1] - dates[0]).days >= 366:
        raise ValueError("historical dates must be increasing and cover at most 366 calendar days")
    params = {key: payload[key] for key in ("symbol_id", "start_date", "end_date", "bar_minutes")}
    # Provider-owned pure validation also checks the current exchange-local date.
    from .questrade import validate_history_params
    normalized = validate_history_params(params)
    return request_id, normalized


def _safe_error(error):
    # Only the provider's explicitly safe exception class may carry messages.
    try:
        from .questrade import QuestradeError
    except ImportError:
        QuestradeError = ()
    if isinstance(error, QuestradeError):
        return str(error)[:500]
    return "Questrade request failed. Check the connection and try again."


def _public_connection(value):
    if not isinstance(value, dict):
        raise ValueError("provider returned invalid connection state")
    states = {"disconnected", "connecting", "connected", "expired"}
    expiry = value.get("expires_at")
    if not isinstance(expiry, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(?:Z|\+00:00)", expiry):
        expiry = None
    progress = value.get("history_progress")
    if isinstance(progress, dict):
        progress = {key: progress[key] for key in ("completed_chunks", "total_chunks", "rows")
                    if type(progress.get(key)) is int and 0 <= progress[key] <= 1_000_000_000}
    else:
        progress = None
    return {"provider": "Questrade", "connected": value.get("connected") is True,
            "state": value.get("state") if value.get("state") in states else "disconnected",
            "expires_at": expiry, "can_refresh": value.get("can_refresh") is True,
            "history_progress": progress}


class ProviderJobs:
    def __init__(self, store, client=None):
        self.store = store
        self._client = client
        self._lock = threading.RLock()
        self._jobs = OrderedDict()
        self._requests = {}
        self._active_id = None
        self._thread = None
        self._connecting = False
        self._disconnecting = False
        self._closed = False

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from .questrade import QuestradeClient
                self._client = QuestradeClient()
            return self._client

    @staticmethod
    def _public(job):
        public = {key: value for key, value in job.items() if key in {"id", "status", "dataset", "error"}}
        public["request_id"] = job["_request_id"]
        return copy.deepcopy(public)

    def latest(self):
        with self._lock:
            return self._public(next(reversed(self._jobs.values()))) if self._jobs else None

    def get(self, job_id):
        with self._lock:
            if job_id not in self._jobs:
                raise NotFound("provider job not found")
            return self._public(self._jobs[job_id])

    def connection_status(self):
        try:
            return _public_connection(self._get_client().status())
        except Exception as error:
            raise ValueError(_safe_error(error)) from None

    def status(self):
        return {"connection": self.connection_status(), "job": self.latest()}

    def connect(self, payload):
        exact_object(payload, {"refresh_token"}, "Questrade connection")
        token = payload["refresh_token"]
        if not isinstance(token, str) or not 1 <= len(token) <= 8192 or any(char.isspace() or ord(char) < 33 for char in token):
            raise ValueError("refresh_token must be a nonempty bounded token without whitespace")
        with self._lock:
            if self._closed:
                raise Conflict("provider service is shutting down")
            if self._active_id is not None or self._connecting or self._disconnecting:
                raise Conflict("finish or cancel the active provider operation before reconnecting")
            self._connecting = True
        try:
            return _public_connection(self._get_client().connect(token))
        except Exception as error:
            raise ValueError(_safe_error(error)) from None
        finally:
            with self._lock:
                self._connecting = False

    def search_symbols(self, prefix):
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", prefix):
            raise ValueError("prefix must contain 1-32 letters, digits, dots, underscores or hyphens")
        with self._lock:
            if self._closed:
                raise Conflict("provider service is shutting down")
        try:
            results = self._get_client().search_symbols(prefix)
            if not isinstance(results, list):
                raise ValueError("provider returned invalid symbol results")
            public = []
            for result in results[:50]:
                if (not isinstance(result, dict) or type(result.get("id")) is not int or
                        not 1 <= result["id"] <= 2147483647 or not isinstance(result.get("symbol"), str) or
                        not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", result["symbol"])):
                    raise ValueError("provider returned invalid symbol results")
                item = {"id": result["id"], "symbol": result["symbol"],
                        "description": str(result.get("description", ""))[:200]}
                if isinstance(result.get("currency"), str) and re.fullmatch(r"[A-Z]{3}", result["currency"]):
                    item["currency"] = result["currency"]
                public.append(item)
            return public
        except Exception as error:
            raise ValueError(_safe_error(error)) from None

    def start(self, payload):
        request_id, params = _validate_request(payload)
        document = canonical(params)
        with self._lock:
            if self._closed:
                raise Conflict("provider service is shutting down")
            if request_id in self._requests:
                job = self._jobs[self._requests[request_id]]
                if job["_payload"] != document:
                    raise Conflict("request_id already belongs to a different provider import")
                return self._public(job), False
            if self._active_id is not None or self._connecting or self._disconnecting:
                raise Conflict("one historical download may run at a time")
            try:
                connection = _public_connection(self._get_client().status())
                if not (connection["connected"] or connection["can_refresh"]):
                    raise Conflict("connect Questrade before starting a historical import")
            except Conflict:
                raise
            except Exception as error:
                raise ValueError(_safe_error(error)) from None
            while len(self._jobs) >= 20:
                old_id, old = self._jobs.popitem(last=False)
                self._requests.pop(old["_request_id"], None)
            job_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            job = {"id": job_id, "status": "queued", "_request_id": request_id,
                   "_payload": document, "_cancel": cancel_event}
            self._jobs[job_id] = job
            self._requests[request_id] = job_id
            self._active_id = job_id
            self._thread = threading.Thread(target=self._run, args=(job_id, params, cancel_event), daemon=True,
                                            name="questrade-history")
            try:
                self._thread.start()
            except Exception:
                job["status"] = "failed"
                job["error"] = "Historical download could not be started."
                self._active_id = None
                self._thread = None
                raise ValueError(job["error"]) from None
            return self._public(job), True

    def _run(self, job_id, params, cancel_event):
        with self._lock:
            job = self._jobs[job_id]
            if cancel_event.is_set() or self._closed:
                job["status"] = "cancelled"
                self._active_id = None
                return
            job["status"] = "running"
        try:
            metadata, contents = self._get_client().historical_import(params, cancel_event=cancel_event)
            # Cancellation and publication share one lock. A successful cancel
            # therefore happens either before this commit (no dataset), or after
            # completion (terminal conflict); it never reports a false cancellation.
            with self._lock:
                if cancel_event.is_set() or self._closed or job["status"] == "cancelled":
                    job["status"] = "cancelled"
                    return
                try:
                    dataset, _ = self.store.import_dataset(metadata, contents)
                except Exception:
                    raise ValueError("dataset publication failed") from None
                job["dataset"] = dataset
                job["status"] = "completed"
        except Exception as error:
            with self._lock:
                if cancel_event.is_set() or self._closed or job["status"] == "cancelled":
                    job["status"] = "cancelled"
                else:
                    job["status"] = "failed"
                    job["error"] = _safe_error(error)
        finally:
            with self._lock:
                if self._active_id == job_id:
                    self._active_id = None

    def cancel(self, payload):
        exact_object(payload, {"job_id"}, "Provider cancellation")
        job_id = payload["job_id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("job_id must identify a provider job")
        with self._lock:
            if job_id not in self._jobs:
                raise NotFound("provider job not found")
            job = self._jobs[job_id]
            if job["status"] in {"completed", "failed"}:
                raise Conflict("provider job has already finished")
            job["_cancel"].set()
            job["status"] = "cancelled"
            return self._public(job)

    def disconnect(self, payload):
        exact_object(payload, set(), "Questrade disconnect")
        with self._lock:
            self._disconnecting = True
            if self._active_id is not None:
                job = self._jobs[self._active_id]
                job["_cancel"].set()
                job["status"] = "cancelled"
        try:
            client = self._get_client()
            client.disconnect()
            return _public_connection(client.status())
        except Exception as error:
            raise ValueError(_safe_error(error)) from None
        finally:
            with self._lock:
                self._disconnecting = False

    def shutdown(self):
        with self._lock:
            self._closed = True
            if self._active_id is not None:
                job = self._jobs[self._active_id]
                job["_cancel"].set()
                job["status"] = "cancelled"
            client, thread = self._client, self._thread
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass  # Shutdown must neither leak credentials nor publish a partial job.
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)
