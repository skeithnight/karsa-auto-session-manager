"""Research CLI — Command-line interface for the Quant CI/CD Platform.

Commands:
    dataset    Manage reproducible datasets
    experiment Run an experiment from a YAML manifest
    compare    Compare two experiments
    leaderboard View experiment rankings
    validate   Run statistical validation (Monte Carlo / Bootstrapping)
    promote    Promote an experiment to Shadow deployment
"""

import argparse
import sys
from loguru import logger

def cmd_dataset(args: argparse.Namespace) -> None:
    logger.info("Dataset command: %s %s", args.action, args.symbol)

def cmd_experiment(args: argparse.Namespace) -> None:
    logger.info("Running experiment from manifest: %s", args.manifest)

def cmd_compare(args: argparse.Namespace) -> None:
    logger.info("Comparing experiments: %s vs %s", args.exp_a, args.exp_b)

def cmd_leaderboard(args: argparse.Namespace) -> None:
    logger.info("Displaying research leaderboard...")

def cmd_validate(args: argparse.Namespace) -> None:
    logger.info("Validating experiment: %s", args.exp_id)

def cmd_promote(args: argparse.Namespace) -> None:
    logger.info("Promoting experiment %s to Shadow", args.exp_id)

def main() -> None:
    parser = argparse.ArgumentParser(description="Karsa Research Operating System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # dataset
    p_dataset = subparsers.add_parser("dataset", help="Manage datasets")
    p_dataset.add_argument("action", choices=["download", "list"])
    p_dataset.add_argument("symbol", nargs="?", help="Symbol (e.g. BTCUSDT)")
    p_dataset.set_defaults(func=cmd_dataset)

    # experiment
    p_experiment = subparsers.add_parser("experiment", help="Run experiments")
    p_experiment.add_argument("action", choices=["run"])
    p_experiment.add_argument("manifest", help="Path to YAML manifest")
    p_experiment.set_defaults(func=cmd_experiment)

    # compare
    p_compare = subparsers.add_parser("compare", help="Compare two experiments")
    p_compare.add_argument("exp_a", help="First experiment ID")
    p_compare.add_argument("exp_b", help="Second experiment ID")
    p_compare.set_defaults(func=cmd_compare)

    # leaderboard
    p_leaderboard = subparsers.add_parser("leaderboard", help="View leaderboard")
    p_leaderboard.set_defaults(func=cmd_leaderboard)

    # validate
    p_validate = subparsers.add_parser("validate", help="Statistical validation")
    p_validate.add_argument("exp_id", help="Experiment ID to validate")
    p_validate.set_defaults(func=cmd_validate)

    # promote
    p_promote = subparsers.add_parser("promote", help="Promote to Shadow")
    p_promote.add_argument("exp_id", help="Experiment ID to promote")
    p_promote.set_defaults(func=cmd_promote)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
