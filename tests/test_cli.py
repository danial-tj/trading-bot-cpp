"""Black-box CLI contract, validation, reproducibility and parameter wiring."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--engine', required=True)
args, remaining = parser.parse_known_args()
ENGINE = str(Path(args.engine).resolve())
ROOT = Path(__file__).resolve().parents[1]

class CliTests(unittest.TestCase):
    def invoke(self, *args, good=True):
        proc = subprocess.run([ENGINE, *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode == 0, good, proc.stderr)
        return json.loads(proc.stdout if good else proc.stderr)

    def test_all_strategies_and_cents(self):
        for strategy, dataset in [('SMA_CROSSOVER','daily_demo'),('EMA_CROSSOVER','daily_demo'),('RSI','daily_demo'),('VWAP_OPENING','opening_demo')]:
            with self.subTest(strategy=strategy):
                report = self.invoke('--data',f'data/{dataset}.csv','--strategy',strategy)
                result = report['results']
                self.assertEqual(report['schema_version'],1)
                self.assertGreater(result['total_trades'],0)
                self.assertAlmostEqual(result['final_equity']*100,result['final_equity_cents'],places=5)
                self.assertAlmostEqual(result['final_equity']-10000,result['realized_pnl']+result['unrealized_pnl'],places=6)
                self.assertEqual(len(result['equity_curve']),len(result['equity_timestamps']))
                self.assertEqual(len(report['benchmark']['equity_curve']),len(result['equity_curve']))
                self.assertIsNone(result['annualized_return'])
                self.assertFalse(report['effective_config']['backtesting']['enable_short_selling'])

    def test_repeated_output_is_identical(self):
        first = self.invoke('--data','data/daily_demo.csv')
        self.assertEqual(first,self.invoke('--data','data/daily_demo.csv'))

    def test_parameter_and_fee_wiring(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)/'config.json'
            baseline = self.invoke('--data','data/daily_demo.csv')
            config.write_text(json.dumps({'backtesting':{'commission_rate':0,'slippage':0},'strategies':{'SMA_CROSSOVER':{'short_period':2,'long_period':4}}}))
            changed = self.invoke('--data','data/daily_demo.csv','--config',str(config))
            self.assertEqual(changed['results']['total_fees_cents'],0)
            self.assertNotEqual(baseline['results']['trades'],changed['results']['trades'])

    def test_invalid_inputs_are_errors(self):
        for args in [[], ['--data','missing.csv'], ['--data','data/daily_demo.csv','--config','missing.json'],
                     ['--data','data/daily_demo.csv','--strategy','UNKNOWN'], ['--live'], ['--data'],
                     ['--data','data/daily_demo.csv','--data','data/daily_demo.csv']]:
            with self.subTest(args=args): self.assertIn('error',self.invoke(*args,good=False))

    def test_benchmark_rounds_fee_before_affordability(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)/'prices.csv'; config=Path(directory)/'config.json'
            data.write_text('timestamp,open,high,low,close,volume\n2024-01-02,1,1,1,1,100\n2024-01-03,1,1,1,1,100\n')
            for fee, quantity in [(.004,100),(.005,99)]:
                config.write_text(json.dumps({'backtesting':{'initial_capital':100,'commission_fixed':fee,'commission_rate':0,'slippage':0}}))
                result=self.invoke('--data',str(data),'--config',str(config))
                self.assertEqual(result['benchmark']['quantity'],quantity)

    def test_invalid_config_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json'
            for invalid in ['{', '{"strategies":{"RSI":{"rsi_period":1.2}}}', '{"backtesting":{"initial_capital":NaN}}']:
                config.write_text(invalid)
                self.invoke('--data','data/daily_demo.csv','--config',str(config),good=False)
            data=Path(directory)/'bad.csv'
            data.write_text('timestamp,open,high,low,close,volume\n2024-01-01,100,99,101,100,100\n')
            self.invoke('--data',str(data),good=False)

    def test_output_file_and_range_warmup(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json'; output=Path(directory)/'result.json'
            config.write_text(json.dumps({'backtesting':{'start_date':'2024-04-01','end_date':'2024-06-30'}}))
            proc=subprocess.run([ENGINE,'--data','data/daily_demo.csv','--config',str(config),'--output',str(output)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual(proc.stdout,'')
            result=json.loads(output.read_text())
            for trade in result['results']['trades']:
                self.assertTrue('2024-04-01' <= trade['timestamp'][:10] <= '2024-06-30')
            self.assertEqual(len(result['benchmark']['equity_curve']),len(result['results']['equity_curve']))

if __name__ == '__main__': unittest.main(argv=['test_cli', *remaining],verbosity=2)
