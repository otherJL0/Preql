import argparse
import json
import time
from itertools import chain
from pathlib import Path

from . import Preql, Signal, __version__, settings

parser = argparse.ArgumentParser(description='Preql command-line interface (aka REPL)')
parser.add_argument(
    '-i',
    '--interactive',
    action='store_true',
    default=False,
    help="enter interactive mode after running the script",
)
parser.add_argument('-v', '--version', action='store_true', help="print version")
parser.add_argument(
    '--install-jupyter',
    action='store_true',
    help="installs the Preql plugin for Jupyter notebook",
)
parser.add_argument(
    '--print-sql', action='store_true', help="print the SQL code that's being executed"
)
parser.add_argument('-f', '--file', type=str, help='path to a Preql script to run')
parser.add_argument('-m', '--module', type=str, help='name of a Preql module to run')
parser.add_argument(
    '--time', action='store_true', help='displays how long the script ran'
)
parser.add_argument(
    '-c',
    '--config',
    type=str,
    help='path to a JSON configuration file for Preql (default: ~/.preql_conf.json)',
)
parser.add_argument(
    'database',
    type=str,
    nargs='?',
    default=None,
    help="database url (postgres://user:password@host:port/db_name",
)
parser.add_argument(
    '--python-traceback',
    action='store_true',
    help="Show the Python traceback when an exception causes the interpreter to quit",
)


def find_dot_preql():
    cwd = Path.cwd()
    for p in chain([cwd], cwd.parents):
        dot_preql = p / ".preql"
        if dot_preql.exists():
            return dot_preql


def update_settings(path):
    config = json.load(path.open())
    if 'debug' in config:
        settings.debug = config['debug']
    if 'color_scheme' in config:
        settings.color_theme.update(config['color_scheme'])


def main():
    args = parser.parse_args()

    if args.version:
        print(__version__)

    if args.install_jupyter:
        from .jup_kernel.install import main as install_jupyter

        install_jupyter([])
        print(
            "Install successful. To start working, run 'jupyter notebook' and create a new Preql notebook."
        )
        return

    from pathlib import Path

    if args.config:
        update_settings(Path(args.config))
    else:
        config_path = Path.home() / '.preql_conf.json'
        if config_path.exists():
            update_settings(config_path)

    kw = {'print_sql': args.print_sql}
    if args.database:
        kw['db_uri'] = args.database
        kw['auto_create'] = True
    p = Preql(**kw)

    interactive = args.interactive

    error_code = 0
    start = time.time()
    try:
        if args.file:
            p.load(args.file)
        elif args.module:
            p('import ' + args.module)
        elif args.version or args.install_jupyter:
            pass
        else:
            dot_preql = find_dot_preql()
            if dot_preql:
                print("Auto-running", dot_preql)
                p._run_code(dot_preql.read_text(), dot_preql)

            interactive = True
    except Signal as e:
        p._display.print_exception(e)
        error_code = -1
        if args.python_traceback:
            raise
    except KeyboardInterrupt:
        print("Interrupted (Ctrl+C)")

    end = time.time()
    if args.time:
        print('Script took %.2f seconds to run' % (end - start))

    if interactive:
        p.load_all_tables()
        p.start_repl()
    else:
        return error_code


if __name__ == '__main__':
    main()
