#include "backtester/backtester.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>

namespace TradingBot {
namespace {
std::string action(SignalType type) {
    switch (type) {
        case SignalType::BUY: return "BUY";
        case SignalType::SELL: return "SELL";
        case SignalType::SHORT: return "SHORT";
        case SignalType::COVER: return "COVER";
        default: return "HOLD";
    }
}
std::int64_t whole_shares(double quantity) {
    if (!std::isfinite(quantity) || quantity <= 0 || std::floor(quantity) != quantity ||
        quantity >= static_cast<double>(std::numeric_limits<std::int64_t>::max()))
        throw std::invalid_argument("quantity must be positive whole shares within int64 range");
    return static_cast<std::int64_t>(quantity);
}
}

bool Backtester::initialize(const BacktestConfig& config) {
    if (!std::isfinite(config.initial_capital) || config.initial_capital <= 0 ||
        !std::isfinite(config.commission_rate) || config.commission_rate < 0 || config.commission_rate > 1 ||
        !std::isfinite(config.commission_fixed) || config.commission_fixed < 0 ||
        !std::isfinite(config.slippage) || config.slippage < 0 || config.slippage >= 1 ||
        config.enable_short_selling || config.symbol.empty()) return false;
    for (const auto& date : {config.start_date, config.end_date})
        if (!date.empty() && (date.size() != 10 || !CSVParser::valid_timestamp(date))) return false;
    if (!config.start_date.empty() && !config.end_date.empty() && config.start_date > config.end_date) return false;
    try { if (to_cents(config.initial_capital) <= 0) return false; (void)to_cents(config.commission_fixed); }
    catch (const std::exception&) { return false; }
    config_ = config;
    return true;
}
BacktestResults Backtester::run_backtest(std::shared_ptr<Strategy> strategy,
                                        std::shared_ptr<CSVParser> parser,
                                        std::shared_ptr<RiskManager> risk) {
    results_ = {};
    if (!strategy || !parser || !risk) throw std::invalid_argument("strategy, parser and risk manager are required");
    if (!parser->validate_data()) throw std::invalid_argument("backtest requires nonempty validated market data");
    if (!initialize(config_)) throw std::invalid_argument("invalid backtest configuration");
    const auto parameters = strategy->get_parameters();
    if (!strategy->initialize(parameters)) throw std::invalid_argument("strategy initialization failed");
    const auto vwap_strategy = std::dynamic_pointer_cast<VWAPOpeningStrategy>(strategy);
    results_.strategy_diagnostics.available = static_cast<bool>(vwap_strategy);
    size_t first = 0;
    while (first < parser->get_data_count() && !config_.start_date.empty() && parser->get_data(first).session_date < config_.start_date) ++first;
    if (first == parser->get_data_count() || (!config_.end_date.empty() && parser->get_data(first).session_date > config_.end_date))
        throw std::invalid_argument("selected date range has no market data");

    Ledger ledger(config_.initial_capital, parser->get_data(first).timestamp);
    results_.initial_cash_cents = ledger.cash_cents();
    const double initial = from_cents(ledger.cash_cents());
    results_.equity_curve.push_back(initial);
    results_.equity_timestamps.push_back(parser->get_data(first).timestamp);
    Position position;
    position.symbol = config_.symbol;
    PortfolioState portfolio;
    double peak = initial, daily_start = initial, last_equity = initial, last_mark = parser->get_data(first).open;
    std::string session;
    bool daily_locked = false;
    std::optional<TradingSignal> pending;
    auto update = [&](double mark) {
        portfolio.cash = from_cents(ledger.cash_cents());
        portfolio.quantity = static_cast<double>(ledger.quantity());
        portfolio.total_value = from_cents(ledger.equity_cents(mark));
        portfolio.cost_basis = from_cents(ledger.cost_basis_cents());
        portfolio.realized_pnl = from_cents(ledger.realized_pnl_cents());
        portfolio.unrealized_pnl = from_cents(ledger.unrealized_pnl_cents(mark));
        portfolio.current_drawdown = risk->calculate_drawdown(peak, portfolio.total_value);
        portfolio.max_drawdown = std::max(portfolio.max_drawdown, portfolio.current_drawdown);
        portfolio.daily_start_value = daily_start;
        portfolio.daily_loss = risk->calculate_drawdown(daily_start, portfolio.total_value);
        if (portfolio.daily_loss >= risk->get_risk_parameters().max_daily_loss) daily_locked = true;
        portfolio.daily_loss_locked = daily_locked;
        position.quantity = portfolio.quantity;
        position.avg_price = ledger.average_cost();
    };
    auto reject = [&](const TradingSignal& signal, const std::string& now, const std::string& reason) {
        results_.rejections.push_back({now, action(signal.type), reason, signal.timestamp});
    };
    auto generated = [&](const MarketData& bar) {
        try { return strategy->generate_signal(bar, position); }
        catch (const std::exception& error) {
            throw std::runtime_error("strategy " + strategy->get_name() + " failed at " + bar.timestamp + ": " + error.what());
        }
    };
    auto record_diagnostics = [&](const MarketData& bar, const TradingSignal& raw_signal) {
        if (!vwap_strategy) return;
        auto& summary = results_.strategy_diagnostics;
        const auto& state = vwap_strategy->diagnostics();
        ++summary.observed_bars;
        if (summary.first_evaluated_timestamp.empty()) summary.first_evaluated_timestamp = bar.timestamp;
        summary.last_evaluated_timestamp = bar.timestamp;
        const int open = static_cast<int>(parameters.at("session_open_minute"));
        const int close = static_cast<int>(parameters.at("session_close_minute"));
        const int completion = bar.minute_of_day + static_cast<int>(parameters.at("bar_minutes"));
        const bool regular = bar.is_intraday && bar.minute_of_day >= open && completion <= close;
        // Readiness is available indicator history, independent of candle/trend
        // alignment, existing exposure, an earlier entry attempt or risk rejection.
        const bool opening = regular && completion < open + parameters.at("opening_window_minutes") &&
            completion < close - parameters.at("exit_buffer_minutes");
        summary.last_bar_regular_session = regular;
        summary.last_diagnostics = state;
        if (regular) { ++summary.evaluated_bars; summary.last_regular_timestamp = bar.timestamp; }
        if (opening) {
            ++summary.opening_bars;
            if (state.intraday_ready) ++summary.intraday_ready_opening_bars;
            if (state.trend_ready) ++summary.trend_ready_opening_bars;
            if (state.intraday_ready && state.trend_ready) {
                ++summary.ready_opening_bars;
                if (summary.first_ready_timestamp.empty()) summary.first_ready_timestamp = bar.timestamp;
            }
        }
        if (position.quantity > 0) ++summary.bars_with_open_position;
        if (raw_signal.type == SignalType::BUY) ++summary.long_signals;
        else if (raw_signal.type == SignalType::SHORT) ++summary.short_signals;
        else if (raw_signal.type == SignalType::HOLD) {
            ++summary.hold_reasons[raw_signal.reason];
            if (opening) ++summary.opening_hold_reasons[raw_signal.reason];
        }
    };

    for (size_t i = 0; i < parser->get_data_count(); ++i) {
        const auto& bar = parser->get_data(i);
        if (!config_.end_date.empty() && bar.session_date > config_.end_date) break;
        if (i < first) {
            (void)generated(bar);
            if (vwap_strategy) ++results_.strategy_diagnostics.warmup_bars;
            continue;
        }
        if (session != bar.session_date) {
            session = bar.session_date;
            daily_start = last_equity; // Overnight gaps belong to the new trading day.
            daily_locked = false;
        }
        update(bar.open);
        if (pending) {
            auto signal = *pending;
            pending.reset();
            if (!signal.valid_until.empty() && bar.timestamp >= signal.valid_until) {
                reject(signal, bar.timestamp, "signal expired before the next available open");
            } else {
                // Next-open execution must not read this bar's later high/low/close/volume.
                // The simulation assumes the provided open is executable; no liquidity model.
                const bool buy = signal.type == SignalType::BUY;
                const auto price_cents = to_cents(bar.open * (buy ? 1 + config_.slippage : 1 - config_.slippage));
                signal.price = from_cents(price_cents);
                auto reason = risk->rejection_reason(signal, portfolio);
                if (reason.empty()) {
                    try {
                        double desired = signal.quantity;
                        if (!std::isfinite(desired) || desired < 0) throw std::invalid_argument("quantity must be finite and nonnegative");
                        if (desired == 0) {
                            if (buy) {
                                const long double capacity = static_cast<long double>(ledger.equity_cents(bar.open)) *
                                    risk->get_risk_parameters().max_position_size;
                                const auto total_allowed = std::floor((capacity + 0.000001L) / price_cents);
                                const auto remaining = std::max(0.0L, total_allowed - ledger.quantity());
                                desired = static_cast<double>(std::min(remaining, static_cast<long double>(ledger.cash_cents() / price_cents)));
                            } else desired = static_cast<double>(ledger.quantity());
                            if (buy && desired > 0) {
                                // Find affordable whole shares using the exact fee rounding
                                // used by the fill, rather than a floating-point cost estimate.
                                std::int64_t low = 0, high = whole_shares(desired);
                                while (low < high) {
                                    const auto span = high - low;
                                    const auto middle = low + span / 2 + span % 2;
                                    const auto value = checked_multiply(price_cents, middle);
                                    const auto fee = to_cents(config_.commission_fixed + from_cents(value) * config_.commission_rate);
                                    if (value <= ledger.cash_cents() && fee <= ledger.cash_cents() - value) low = middle;
                                    else high = middle - 1;
                                }
                                desired = static_cast<double>(low);
                            }
                        }
                        const auto quantity = whole_shares(desired);
                        const auto gross = checked_multiply(price_cents, quantity);
                        const auto fee = to_cents(config_.commission_fixed + from_cents(gross) * config_.commission_rate);
                        if (buy) {
                            const long double exposure = static_cast<long double>(checked_add(ledger.quantity(), quantity)) * price_cents;
                            const long double capacity = static_cast<long double>(ledger.equity_cents(bar.open)) * risk->get_risk_parameters().max_position_size;
                            if (exposure > capacity + 0.000001L) throw std::invalid_argument("maximum position size exceeded");
                        }
                        const auto before_realized = ledger.realized_pnl_cents();
                        const auto event = buy ? ledger.buy_cents(quantity, price_cents, fee, bar.timestamp) :
                                                 ledger.sell_cents(quantity, price_cents, fee, bar.timestamp);
                        Trade trade;
                        trade.timestamp = bar.timestamp; trade.signal_timestamp = signal.timestamp;
                        trade.action = action(signal.type); trade.reason = signal.reason;
                        trade.price = event.price; trade.quantity = static_cast<double>(quantity);
                        trade.commission = event.fee; trade.pnl = from_cents(ledger.realized_pnl_cents() - before_realized);
                        trade.slippage = from_cents(checked_multiply(std::abs(price_cents - to_cents(bar.open)), quantity));
                        trade.cash_after = from_cents(ledger.cash_cents()); trade.position_after = static_cast<double>(ledger.quantity());
                        results_.total_slippage += trade.slippage;
                        results_.trades.push_back(trade);
                    } catch (const std::invalid_argument& error) { reject(signal, bar.timestamp, error.what()); }
                    // Overflow and runtime failures abort the run; they are not silently treated as valid no-trade results.
                } else reject(signal, bar.timestamp, reason);
            }
        }
        update(bar.close);
        peak = std::max(peak, portfolio.total_value);
        last_equity = portfolio.total_value;
        last_mark = bar.close;
        ledger.mark(bar.close, bar.timestamp);
        results_.equity_curve.push_back(portfolio.total_value);
        results_.equity_timestamps.push_back(bar.timestamp);

        auto signal = generated(bar); // Only this completed bar and older bars are visible.
        record_diagnostics(bar, signal); // Save actual strategy output before risk overrides.
        signal.timestamp = bar.timestamp;
        const auto closure = risk->closure_reason(position, bar, portfolio);
        if (!closure.empty()) {
            signal.type = SignalType::SELL; signal.price = bar.close; signal.quantity = position.quantity;
            signal.reason = closure; signal.valid_until.clear();
        }
        if (signal.type == SignalType::SHORT || signal.type == SignalType::COVER) {
            reject(signal, bar.timestamp, "short execution is disabled; signal retained for analysis");
        } else if (signal.type != SignalType::HOLD) {
            pending = signal;
        }
    }
    if (pending) reject(*pending, pending->timestamp, "no next bar available; final signal cancelled");
    results_.final_cash_cents = ledger.cash_cents();
    results_.final_equity_cents = ledger.equity_cents(last_mark);
    results_.cost_basis_cents = ledger.cost_basis_cents();
    results_.realized_pnl_cents = ledger.realized_pnl_cents();
    results_.unrealized_pnl_cents = ledger.unrealized_pnl_cents(last_mark);
    results_.total_fees_cents = ledger.fees_cents();
    results_.final_cash = from_cents(results_.final_cash_cents);
    results_.final_equity = from_cents(results_.final_equity_cents);
    results_.final_quantity = static_cast<double>(ledger.quantity());
    results_.cost_basis = from_cents(results_.cost_basis_cents);
    results_.realized_pnl = from_cents(results_.realized_pnl_cents);
    results_.unrealized_pnl = from_cents(results_.unrealized_pnl_cents);
    results_.total_fees = from_cents(results_.total_fees_cents);
    results_.events = ledger.events();
    calculate_statistics();
    return results_;
}
const BacktestConfig& Backtester::get_config() const { return config_; }
void Backtester::set_config(const BacktestConfig& config) {
    if (!initialize(config)) throw std::invalid_argument("invalid backtest configuration");
}
const BacktestResults& Backtester::get_results() const { return results_; }
void Backtester::calculate_statistics() {
    results_.total_trades = static_cast<int>(results_.trades.size());
    double wins = 0, losses = 0;
    for (const auto& trade : results_.trades) {
        if (trade.action != "SELL") continue;
        ++results_.closed_trades; // A closed trade means each realized SELL fill, including partial exits.
        if (trade.pnl > 0) { ++results_.winning_trades; wins += trade.pnl; }
        else if (trade.pnl < 0) { ++results_.losing_trades; losses -= trade.pnl; }
    }
    results_.win_rate = results_.closed_trades ? static_cast<double>(results_.winning_trades) / results_.closed_trades : 0;
    results_.avg_win = results_.winning_trades ? wins / results_.winning_trades : 0;
    results_.avg_loss = results_.losing_trades ? losses / results_.losing_trades : 0;
    if (losses > 0) results_.profit_factor = wins / losses;
    const double initial = from_cents(results_.initial_cash_cents);
    results_.total_return = (results_.final_equity - initial) / initial;
    double peak = initial;
    for (const auto value : results_.equity_curve) {
        peak = std::max(peak, value);
        results_.max_drawdown = std::max(results_.max_drawdown, (peak - value) / peak);
    }
}
}
