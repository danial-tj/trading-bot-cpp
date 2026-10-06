#pragma once
#include "backtester/ledger.h"
#include "data/csv_parser.h"
#include "strategy/strategy.h"
#include "risk/risk_manager.h"
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace TradingBot {
struct Trade {
    std::string timestamp, signal_timestamp, action, reason;
    double price = 0, quantity = 0, commission = 0, pnl = 0, slippage = 0;
    double cash_after = 0, position_after = 0;
};
struct OrderRejection {
    std::string timestamp, action, reason;
    std::string signal_timestamp;
};
struct StrategyDiagnosticsSummary {
    bool available = false;
    // In-range counts only. Warmup bars are reported separately, never mixed in.
    std::size_t observed_bars = 0, evaluated_bars = 0, warmup_bars = 0;
    std::size_t opening_bars = 0, intraday_ready_opening_bars = 0;
    std::size_t trend_ready_opening_bars = 0, ready_opening_bars = 0;
    std::size_t long_signals = 0, short_signals = 0, bars_with_open_position = 0;
    std::map<std::string, std::size_t> hold_reasons, opening_hold_reasons;
    std::string first_evaluated_timestamp, last_evaluated_timestamp, first_ready_timestamp;
    std::string last_regular_timestamp;
    bool last_bar_regular_session = false;
    // The actual final strategy state may include indicator history from warmup.
    VWAPDiagnostics last_diagnostics;
};
struct BacktestResults {
    double total_return = 0;
    double annualized_return = std::numeric_limits<double>::quiet_NaN();
    double sharpe_ratio = std::numeric_limits<double>::quiet_NaN();
    double max_drawdown = 0, win_rate = 0, avg_win = 0, avg_loss = 0;
    double profit_factor = std::numeric_limits<double>::quiet_NaN();
    int total_trades = 0, closed_trades = 0, winning_trades = 0, losing_trades = 0;
    double final_cash = 0, final_equity = 0, final_quantity = 0, cost_basis = 0;
    double realized_pnl = 0, unrealized_pnl = 0, total_fees = 0, total_slippage = 0;
    Cents initial_cash_cents = 0, final_cash_cents = 0, final_equity_cents = 0;
    Cents cost_basis_cents = 0, realized_pnl_cents = 0, unrealized_pnl_cents = 0, total_fees_cents = 0;
    std::vector<Trade> trades;
    std::vector<OrderRejection> rejections;
    std::vector<LedgerEvent> events;
    std::vector<double> equity_curve;
    std::vector<std::string> equity_timestamps;
    StrategyDiagnosticsSummary strategy_diagnostics;
};
struct BacktestConfig {
    double initial_capital = 100000, commission_rate = 0.001, commission_fixed = 0, slippage = 0.0001;
    std::string start_date, end_date, symbol = "SIM";
    bool enable_short_selling = false;
};
class Backtester {
public:
    bool initialize(const BacktestConfig& config);
    BacktestResults run_backtest(std::shared_ptr<Strategy> strategy,
                                std::shared_ptr<CSVParser> data_parser,
                                std::shared_ptr<RiskManager> risk_manager);
    const BacktestConfig& get_config() const;
    void set_config(const BacktestConfig& config);
    const BacktestResults& get_results() const;
private:
    BacktestConfig config_;
    BacktestResults results_;
    void calculate_statistics();
};
}
