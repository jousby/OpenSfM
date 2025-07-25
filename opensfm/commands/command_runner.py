# pyre-strict
import argparse
from types import ModuleType
from typing import Callable, ContextManager, List

from opensfm import log
from opensfm.dataset import DataSet


def command_runner(
    all_commands_types: List[ModuleType],
    dataset_factory: Callable[[str, str], ContextManager[DataSet]],
    dataset_choices: List[str],
) -> None:
    """Main entry point for running the passed SfM commands types."""
    log.setup()

    # Create the top-level parser
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        help="Command to run", dest="command", metavar="command"
    )

    command_objects = [c.Command() for c in all_commands_types]

    for command in command_objects:
        subparser = subparsers.add_parser(command.name, help=command.help)
        command.add_arguments(subparser)
        subparser.add_argument(
            "--dataset-type",
            type=str,
            required=False,
            default="opensfm",
            choices=dataset_choices,
        )

    # Parse arguments
    args = parser.parse_args()

    # Instanciate datast
    with dataset_factory(args.dataset, args.dataset_type) as data:
        # Run the selected subcommand
        for command in command_objects:
            if args.command == command.name:
                command.run(data, args)
