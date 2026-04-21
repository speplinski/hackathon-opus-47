"""Layer modules — each exposes `run(run_ctx: RunContext) -> None`.

Invariant: every layer reads from `data/derived/l{n-1}/*.jsonl` and writes
to `data/derived/l{n}/*.jsonl`. See ARCHITECTURE.md §3 for the full
repository layout and §5.1 for the DAG runner contract.
"""
