"""Launch a reproducible CLI example or the local browser demo."""
import argparse
from pathlib import Path
import subprocess
import sys
from service.worker import find_engine

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--engine')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--dataset', choices=['sample','opening_demo'], default='opening_demo')
    parser.add_argument('--strategy', choices=['SMA_CROSSOVER','EMA_CROSSOVER','RSI','VWAP_OPENING'], default='VWAP_OPENING')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, default=Path('report.json'))
    args = parser.parse_args()
    try:
        engine = find_engine(args.engine)
    except (FileNotFoundError, ValueError) as error:
        parser.exit(1, str(error)+'\nBuild the project first; see README.md.\n')
    if args.serve:
        return subprocess.call([sys.executable,'-m','service.server','--engine',str(engine),'--port',str(args.port)],cwd=ROOT)
    dataset=ROOT/'data'/('daily_demo.csv' if args.dataset=='sample' else 'opening_demo.csv')
    output=args.output.resolve()
    command=[str(engine),'--data',str(dataset),'--strategy',args.strategy,'--output',str(output)]
    if args.config:
        command+=['--config',str(args.config.resolve())]
    completed=subprocess.run(command,cwd=ROOT)
    if completed.returncode==0:
        print('Saved simulation to '+str(output))
    return completed.returncode

if __name__=='__main__':
    raise SystemExit(main())
