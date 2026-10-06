"""Local-only HTTP interface; validated dataset imports and no arbitrary paths."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit, unquote, parse_qs

from .chart_data import chart_for_run
from .provider_jobs import ProviderJobs
from .store import Store, Conflict, NotFound, canonical
from .validation import ROOT, MAX_BODY_BYTES, MAX_IMPORT_BODY_BYTES, catalog, validate_request
from .worker import find_engine, worker_loop


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, static=None, questrade_client=None):
        self.store = store
        self.provider_jobs = ProviderJobs(store, questrade_client)
        self.static = (static or ROOT / "service" / "static").resolve()
        self.capacity = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def server_close(self):
        self.provider_jobs.shutdown()
        super().server_close()

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalBacktest/2"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        if self.path.startswith("/api/providers/questrade"):
            # Never log provider query strings or arbitrary request-path values.
            route = urlsplit(self.path).path.rsplit("/", 1)[-1]
            if route not in {"status", "connect", "disconnect", "symbols", "import", "cancel"}:
                route = "unknown"
            logging.getLogger("trading_service.http").info("%s Questrade %s", self.command, route)
            return
        logging.getLogger("trading_service.http").info(format, *args)

    def guard(self):
        port = self.server.server_address[1]
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if self.headers.get("Host", "") not in hosts:
            raise PermissionError("local Host header required")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            raise PermissionError("cross-origin requests are disabled")

    def send_json(self, status, document):
        data = canonical(document).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def request_json(self, max_bytes=MAX_BODY_BYTES):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("chunked requests are unsupported")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type application/json required")
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1 or not re.fullmatch(r"[0-9]{1,8}", values[0]):
            raise ValueError("one valid Content-Length header required")
        length = int(values[0])
        if not 0 < length <= max_bytes:
            # Drain a bounded small overrun so Windows can deliver the error
            # response without resetting a socket with unread request bytes.
            if 0 < length <= min(max_bytes * 2, 64 * 1024):
                self.rfile.read(length)
            raise ValueError(f"request body exceeds the {max_bytes} byte limit or is empty")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        def duplicate_check(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        return json.loads(body, object_pairs_hook=duplicate_check,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON number")))

    def dispatch(self, method):
        try:
            self.guard()
            path = urlsplit(self.path).path
            if path.startswith("/api/providers/questrade/"):
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                jobs = self.server.provider_jobs
                if path != "/api/providers/questrade/symbols" and query:
                    raise ValueError("this provider endpoint does not accept query parameters")
                if method == "GET" and path == "/api/providers/questrade/status":
                    return self.send_json(200, jobs.status())
                if method == "POST" and path == "/api/providers/questrade/connect":
                    return self.send_json(200, {"connection": jobs.connect(self.request_json())})
                if method == "POST" and path == "/api/providers/questrade/disconnect":
                    return self.send_json(200, {"connection": jobs.disconnect(self.request_json())})
                if method == "GET" and path == "/api/providers/questrade/symbols":
                    if set(query) != {"prefix"} or len(query["prefix"]) != 1:
                        raise ValueError("symbols accepts exactly one prefix parameter")
                    return self.send_json(200, {"symbols": jobs.search_symbols(query["prefix"][0])})
                if method == "POST" and path == "/api/providers/questrade/import":
                    job, created = jobs.start(self.request_json())
                    return self.send_json(202 if created else 200, {"job": job})
                if method == "POST" and path == "/api/providers/questrade/cancel":
                    return self.send_json(200, {"job": jobs.cancel(self.request_json())})
                raise NotFound("provider route not found")
            if method == "GET" and path == "/api/catalog":
                return self.send_json(200, catalog(self.server.store.imported_datasets()))
            if method == "POST" and path == "/api/datasets/import":
                from .import_data import validate_import
                payload = self.request_json(MAX_IMPORT_BODY_BYTES)
                if not isinstance(payload, dict):
                    raise ValueError("import payload must be an object")
                import_format = payload.pop("format", "standard")
                if import_format not in ("standard", "tradingview"):
                    raise ValueError("format must be standard or tradingview")
                if import_format == "tradingview":
                    from .tradingview import validate_tradingview_import
                    metadata, content = validate_tradingview_import(payload)
                else:
                    metadata, content = validate_import(payload)
                dataset, created = self.server.store.import_dataset(metadata, content)
                return self.send_json(201 if created else 200, {"dataset": dataset, "created": created})
            if path == "/api/runs":
                if method == "GET":
                    return self.send_json(200, {"runs": self.server.store.list()})
                if method == "POST":
                    imported = {entry["id"]: entry for entry in self.server.store.imported_datasets()}
                    payload = validate_request(self.request_json(), extra_datasets=imported)
                    metadata, content = self.server.store.resolve_dataset(payload["dataset"])
                    run, created = self.server.store.submit(payload, content, dataset_info=metadata)
                    return self.send_json(201 if created else 200, {"run": run, "created": created})
            match = re.fullmatch(r"/api/runs/([a-f0-9]{32})(?:/(result|events|cancel|reconcile|chart))?", path)
            if match:
                run_id, action = match.groups()
                store = self.server.store
                if method == "GET":
                    if action is None:
                        return self.send_json(200, {"run": store.get(run_id)})
                    if action == "result":
                        return self.send_json(200, store.result(run_id))
                    if action == "events":
                        return self.send_json(200, {"events": store.events(run_id)})
                    if action == "reconcile":
                        return self.send_json(200, store.reconcile(run_id))
                    if action == "chart":
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                        if set(query) - {"session"} or any(len(values) != 1 for values in query.values()):
                            raise ValueError("chart accepts only one optional session parameter")
                        return self.send_json(200, chart_for_run(store, run_id, query.get("session", [None])[0]))
                if method == "POST" and action == "cancel":
                    return self.send_json(200, {"run": store.cancel(run_id)})
            if method == "GET" and not path.startswith("/api/"):
                return self.static_file(path)
            raise NotFound("route not found")
        except NotFound as error:
            self.send_json(404, {"error": str(error)})
        except Conflict as error:
            self.send_json(409, {"error": str(error)})
        except PermissionError as error:
            self.send_json(403, {"error": str(error)})
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            self.send_json(400, {"error": str(error)[:500]})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            logging.getLogger("trading_service").exception("request failed")
            self.send_json(500, {"error": "internal service error; inspect local logs"})

    def static_file(self, path):
        name = unquote(path).lstrip("/") or "index.html"
        file = (self.server.static / name).resolve()
        if not file.is_relative_to(self.server.static) or not file.is_file() or file.suffix not in (".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"):
            raise NotFound("file not found")
        data = file.read_bytes()
        self.send_response(200)
        content_type = "font/woff2" if file.suffix == ".woff2" else mimetypes.guess_type(file.name)[0]
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")


def main():
    parser = argparse.ArgumentParser(description="Run the offline trading simulator on localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(ROOT / ".local" / "trading.sqlite3"))
    parser.add_argument("--engine")
    parser.add_argument("--no-worker", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1-65535")
    store = Store(args.db)
    store.initialize()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = LocalServer(("127.0.0.1", args.port), store)
    stop = threading.Event()
    worker = None
    if not args.no_worker:
        worker = threading.Thread(target=worker_loop, args=(store, find_engine(args.engine), stop), daemon=True)
        worker.start()
    print(f"Local simulation: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if worker:
            worker.join(timeout=65)


if __name__ == "__main__":
    main()
