"""``ybcal`` entry point (owner: WP-0; PLAN §3.2).

Dispatch convention (see ``docs/architecture.md``): every subcommand is a :class:`Route` to a
function ``cli_<name>(args: argparse.Namespace) -> int`` in a package-level ``cli`` module, e.g.
``ybcal data fetch`` → ``ybcal.data.cli.cli_fetch``. ``cli.py`` declares the PLAN §3.2 arguments;
a module may add more by defining ``configure_<name>(parser)``. While the target module or function
does not exist, the command prints ``not implemented yet (WP-n)`` and exits 2. A later WP therefore
adds only its own ``<pkg>/cli.py`` and never edits this file.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ybcal import __version__
from ybcal.params.registry import GROUPS, KLASSES, PINNED_COMMIT

EXIT_NOT_IMPLEMENTED = 2

#: ``ybcal study`` targets.
STUDY_TARGETS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "R", "all")
BUDGET_CHOICES: tuple[str, ...] = ("quick", "standard", "deep")


@dataclass(frozen=True)
class Route:
    """Where a subcommand is implemented and which work package owns it."""

    module: str   #: e.g. "ybcal.data.cli"
    func: str     #: e.g. "cli_fetch"
    wp: str       #: e.g. "WP-2"

    @property
    def configure(self) -> str:
        """Optional hook name that adds extra arguments: ``configure_<name>``."""
        return "configure_" + self.func.removeprefix("cli_")


#: (command, subcommand) → Route. Single-level commands use ``(command,)``.
ROUTES: dict[tuple[str, ...], Route] = {
    ("params", "show"): Route("ybcal.params.cli", "cli_show", "WP-0"),
    ("params", "extract"): Route("ybcal.params.cli", "cli_extract", "WP-0"),
    ("params", "check"): Route("ybcal.params.cli", "cli_check", "WP-0"),
    ("params", "doc"): Route("ybcal.params.cli", "cli_doc", "WP-0"),
    ("data", "fetch"): Route("ybcal.data.cli", "cli_fetch", "WP-2"),
    ("data", "import"): Route("ybcal.data.cli", "cli_import", "WP-2"),
    ("data", "synth"): Route("ybcal.data.cli", "cli_synth", "WP-2"),
    ("data", "describe"): Route("ybcal.data.cli", "cli_describe", "WP-2"),
    ("study",): Route("ybcal.studies.cli", "cli_study", "WP-8"),
    ("sensitivity",): Route("ybcal.optimize.cli", "cli_sensitivity", "WP-6"),
    ("recommend",): Route("ybcal.report.cli", "cli_recommend", "WP-8"),
    ("verify",): Route("ybcal.model.cli", "cli_verify", "WP-1"),
    ("devnet", "build"): Route("ybcal.devnet.cli", "cli_build", "WP-9"),
    ("devnet", "run"): Route("ybcal.devnet.cli", "cli_run", "WP-9"),
    ("devnet", "validate"): Route("ybcal.devnet.cli", "cli_validate", "WP-9"),
    ("report", "open"): Route("ybcal.report.cli", "cli_open", "WP-8"),
}


def _import_route_module(route: Route) -> object | None:
    """The route's module, or ``None`` if that module itself does not exist yet.

    An ImportError raised *inside* an existing module propagates (a broken module must be loud).
    """
    try:
        return importlib.import_module(route.module)
    except ModuleNotFoundError as e:
        if e.name is not None and (route.module == e.name or route.module.startswith(e.name + ".")):
            return None
        raise


def resolve(route: Route) -> Callable[[argparse.Namespace], int] | None:
    """The implementing function, or ``None`` while not implemented."""
    mod = _import_route_module(route)
    fn = getattr(mod, route.func, None) if mod is not None else None
    return fn if callable(fn) else None


def _run_route(route: Route) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        fn = resolve(route)
        if fn is None:
            print(f"ybcal {' '.join(args.command_path)}: not implemented yet ({route.wp})", file=sys.stderr)
            return EXIT_NOT_IMPLEMENTED
        return int(fn(args) or 0)
    return run


def _configure(route: Route, parser: argparse.ArgumentParser) -> None:
    """Let the implementing module add arguments (never fatal while it does not exist)."""
    try:
        mod = _import_route_module(route)
    except ImportError:
        return
    hook = getattr(mod, route.configure, None) if mod is not None else None
    if callable(hook):
        hook(parser)


# ---------------------------------------------------------------------------------------------------
# Parser


def _common_run_args(p: argparse.ArgumentParser, *, budget: bool = True) -> None:
    if budget:
        p.add_argument("--budget", choices=BUDGET_CHOICES, default="quick",
                       help="compute budget (default quick)")
    p.add_argument("--policy", default=None, help="policy TOML (default policy/default.toml)")
    p.add_argument("--seed", type=int, default=None, help="RNG seed (default: policy.seed)")
    p.add_argument("--data", action="append", default=[], metavar="FILE",
                   help="data file (repeatable); synthetic data is used when none is given")


def _ycash6_args(p: argparse.ArgumentParser, *, required: bool = False) -> None:
    p.add_argument("--ycash6", required=required, default=None,
                   help="path to a ycash6 clone (default $YBCAL_YCASH6, else the committed snapshot)")
    p.add_argument("--ref", default=PINNED_COMMIT, help=f"git ref to read (default the pin {PINNED_COMMIT})")


def build_parser() -> argparse.ArgumentParser:
    """The full argparse tree of PLAN §3.2."""
    parser = argparse.ArgumentParser(prog="ybcal", description="Yellowback (YED) parameter calibration tool")
    parser.add_argument("--version", action="version",
                        version=f"ybcal {__version__} (ycash6 {PINNED_COMMIT})")
    top = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    subs: dict[tuple[str, ...], argparse.ArgumentParser] = {}

    def group(name: str, help_: str) -> argparse._SubParsersAction:
        g = top.add_parser(name, help=help_)
        return g.add_subparsers(dest="subcommand", metavar="SUBCOMMAND", required=True)

    # params
    pg = group("params", "the parameter registry, extraction from source, drift and invariant checks")
    p = subs[("params", "show")] = pg.add_parser("show", help="print the parameter table")
    p.add_argument("--group", choices=GROUPS, default=None)
    p.add_argument("--class", dest="klass", choices=KLASSES, default=None)
    p.add_argument("--json", action="store_true", help="JSON output")
    _ycash6_args(p)
    p = subs[("params", "extract")] = pg.add_parser("extract", help="extract main/test/regtest sets")
    _ycash6_args(p)
    p.add_argument("--out", default=None, help="write JSON here (default stdout)")
    p = subs[("params", "check")] = pg.add_parser("check", help="drift + invariants; non-zero on failure")
    _ycash6_args(p)
    p.add_argument("--policy", default=None, help="policy TOML for policy-dependent invariants")
    p.add_argument("--release-tip", type=int, default=None, help="release tip height for the M14 lead check")
    p = subs[("params", "doc")] = pg.add_parser("doc", help="regenerate docs/parameters.md from the registry")
    p.add_argument("--out", default="docs/parameters.md")
    p.add_argument("--check", action="store_true", help="exit 1 if the file differs instead of writing")

    # data
    dg = group("data", "price / spread / pool-share / depth data and synthetic paths")
    p = subs[("data", "fetch")] = dg.add_parser("fetch", help="download price data (needs network)")
    p.add_argument("--source", choices=("coingecko", "nonkyc", "tickers"), default="coingecko")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", required=True)
    p = subs[("data", "import")] = dg.add_parser("import", help="import a CSV/JSON file")
    p.add_argument("file")
    p.add_argument("--kind", choices=("price", "spreads", "hashrate", "depth"), default="price")
    p.add_argument("--out", default=None)
    p = subs[("data", "synth")] = dg.add_parser("synth", help="generate synthetic price paths")
    p.add_argument("--model", choices=("gbm", "merton", "garch", "regime", "bootstrap"), default="garch")
    p.add_argument("--calibrate", default=None, metavar="FILE", help="fit the model to this price file")
    p.add_argument("--paths", type=int, default=1000)
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default=None)
    p = subs[("data", "describe")] = dg.add_parser("describe", help="realised vol, drawdowns, gaps, tails")
    p.add_argument("file")

    # single-level commands
    p = subs[("study",)] = top.add_parser("study", help="run one parameter-group study (or all)")
    p.add_argument("group", choices=STUDY_TARGETS)
    _common_run_args(p)
    p.add_argument("--out", default=None, help="output directory")
    p = subs[("sensitivity",)] = top.add_parser("sensitivity", help="joint sensitivity analysis")
    p.add_argument("--method", choices=("morris", "sobol"), default="morris")
    _common_run_args(p)
    p.add_argument("--out", default=None)
    p = subs[("recommend",)] = top.add_parser("recommend", help="all studies, joint pass, report")
    _common_run_args(p)
    p.add_argument("--out", default=None, help="report directory (default reports/<date>-<hash>/)")
    p.add_argument("--synthetic", action="store_true", help="synthetic data only (CI)")
    p.add_argument("--manifest", default=None, help="reproduce the run recorded in this manifest.json")
    subs[("verify",)] = top.add_parser("verify", help="kernel parity vs reference model + C++ examples")

    # devnet
    vg = group("devnet", "regtest devnet of real ycashd nodes (optional)")
    p = subs[("devnet", "build")] = vg.add_parser("build", help="worktree + overlay patch + build (cached)")
    p.add_argument("--overlay", default=None, help="parameter overlay JSON (e.g. recommended.json)")
    _ycash6_args(p)
    p = subs[("devnet", "run")] = vg.add_parser("run", help="up, replay a scenario, scrape, down")
    p.add_argument("--scenario", required=True)
    p.add_argument("--overlay", default=None)
    p.add_argument("--seed", type=int, default=None)
    p = subs[("devnet", "validate")] = vg.add_parser("validate", help="the §6.4 differential suite")
    p.add_argument("--scenario", action="append", default=[], help="limit to these scenarios (repeatable)")

    # report
    rg = group("report", "inspect a finished report")
    p = subs[("report", "open")] = rg.add_parser("open", help="print the report path or serve the HTML")
    p.add_argument("run_dir")
    p.add_argument("--serve", action="store_true")

    for path, sp in subs.items():
        route = ROUTES[path]
        _configure(route, sp)
        sp.set_defaults(_run=_run_route(route), command_path=list(path))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args._run(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
