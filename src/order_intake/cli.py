import argparse
import sys

from .config import load_settings
from .llm.replay import SIMULATIONS, ModelFactory
from .pipeline import Pipeline
from .storage import Store


def parse_simulate(values: list[str] | None) -> dict[str, str]:
    out = {}
    for value in values or []:
        request_id, _, kind = value.partition("=")
        if kind not in SIMULATIONS:
            raise SystemExit(f"--simulate expects REQUEST=one of {SIMULATIONS}, got {value!r}")
        out[request_id] = kind
    return out


def build(args) -> tuple:
    settings = load_settings(llm_mode=getattr(args, "mode", None), db_path=getattr(args, "db", None),
                             model_id=getattr(args, "model", None))
    store = Store(settings.db_path)
    factory = ModelFactory(simulate=parse_simulate(getattr(args, "simulate", None)))
    return settings, store, Pipeline(settings, store, model_factory=factory)


def cmd_process(args) -> int:
    settings, store, pipeline = build(args)
    print(f"model={settings.model_id} mode={settings.llm_mode} db={settings.db_path}")
    only = set(args.only.split(",")) if args.only else None
    for r in pipeline.process_all(only=only, retry_failed=args.retry_failed):
        print(f"  {r['request_id']:<5} {r['status']:<20} {r['action']}")
    print_status(store)
    return 0


def print_status(store: Store) -> None:
    counts = store.status_counts()
    print("status counts:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(empty)")
    print("orders (distinct order references):", store.order_count())


def cmd_status(args) -> int:
    _, store, _ = build(args)
    print_status(store)
    return 0


def cmd_check(args) -> int:
    from .evals.check import run_checks

    settings = load_settings(llm_mode=args.mode, model_id=args.model)
    report = run_checks(settings, settings.root / "reports")
    for r in report["cases"]:
        print(f"  {r['case_id']:<7} {r['request_id']:<4} {'PASS' if r['passed'] else 'FAIL'}  {r['title']}")
        for c in r["checks"]:
            if not c["passed"]:
                print(f"           x {c['name']}: expected {c['expected']!r}, observed {c['observed']!r}")
    for c in report["batch"] + report["simulated_failures"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}  (observed {c['observed']!r})")
    print(f"overall: {'PASS' if report['passed'] else 'FAIL'} -> reports/minimum-demonstration.md")
    return 0 if report["passed"] else 1


def cmd_serve(args) -> int:
    import os

    import uvicorn

    if args.db:
        os.environ["DB_PATH"] = args.db
    if args.mode:
        os.environ["LLM_MODE"] = args.mode
    uvicorn.run("order_intake.web.app:app", host=args.host, port=args.port, reload=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="order-intake", description="AI order intake and exception handling")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--db", help="SQLite path (default var/intake.db or DB_PATH)")
        p.add_argument("--mode", choices=["live", "replay", "auto"], help="LLM mode (default LLM_MODE or replay)")

    p = sub.add_parser("process", help="process request files in data/requests")
    common(p)
    p.add_argument("--model", help="override MODEL_ID")
    p.add_argument("--only", help="comma-separated request IDs")
    p.add_argument("--retry-failed", action="store_true", help="reprocess requests whose status is failed")
    p.add_argument("--simulate", action="append", metavar="REQ=KIND",
                   help=f"labelled simulated failure, KIND in {SIMULATIONS}")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("status", help="print status counts")
    common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="run the minimum-demonstration checks on a fresh temporary database")
    p.add_argument("--mode", choices=["live", "replay", "auto"], help="LLM mode (default LLM_MODE or replay)")
    p.add_argument("--model", help="override MODEL_ID")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("serve", help="run the review web app")
    common(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
