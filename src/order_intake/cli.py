import argparse
import sys
from pathlib import Path

from .config import load_settings
from .llm.replay import SIMULATIONS, ModelFactory
from .pipeline import Pipeline
from .storage import Store


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


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


def cmd_judge(args) -> int:
    from .evals.judge import run_judge

    settings = load_settings()
    out = Path(args.out) if args.out else settings.root / "reports"
    results = run_judge(settings, out, judge_mode=args.mode, pipeline_mode=args.pipeline_mode,
                        samples=args.samples, update_baseline=args.update_baseline)
    for r in results["cases"]:
        print(f"  {r['case_id']:<7} {r['kind']:<10} trajectory={r['trajectory']['verdict']:<5} "
              f"reference={r['reference']['verdict']:<5} judge={r['judge_only']:<20} overall={r['overall']}")
    cal = results["calibration"]
    print(f"judge-only detection {cal['detection']['count']}/{cal['detection']['n']}, false fail "
          f"{cal['false_fail']['count']}/{cal['false_fail']['n']}, excluded {cal['excluded']['count']}")
    print(f"baseline: {results['baseline']['message']}")
    if results["missing_recordings"]:
        print(f"missing recordings: {', '.join(results['missing_recordings'])}")
    print(f"exit {results['exit_code']} -> {out / 'judge-report.md'}")
    return results["exit_code"]


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

    p = sub.add_parser("judge", help="run the offline LLM-judge evaluation on a fresh temporary database")
    p.add_argument("--mode", choices=["replay", "live", "auto"], default="replay", help="judge LLM mode")
    p.add_argument("--pipeline-mode", choices=["replay", "live", "auto"], default="replay",
                   help="pipeline LLM mode; live re-records into a scratch replay directory (drift run), "
                        "auto records only missing calls into replay/")
    p.add_argument("--samples", type=positive_int, default=3, help="judge samples per case (default 3)")
    p.add_argument("--update-baseline", action="store_true", help="write judge-baseline.json from this run")
    p.add_argument("--out", help="report directory (default reports)")
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("serve", help="run the review web app")
    common(p)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
