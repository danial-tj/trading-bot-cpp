#include "strategy/strategy.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <vector>
#include <iomanip>

using namespace TradingBot;
namespace {
void require(bool condition, const std::string& message) { if (!condition) throw std::runtime_error(message); }
MarketData bar(const std::string& stamp, double open, double close, double volume = 1000.0) {
    MarketData result;
    result.timestamp = stamp; result.open = open; result.close = close;
    result.high = std::max(open, close) + .01; result.low = std::min(open, close) - .01; result.volume = volume;
    return result;
}
std::string stamp(int month, int day, int minute) {
    std::ostringstream stream;
    stream << "2025-" << std::setfill('0') << std::setw(2) << month << '-' << std::setw(2) << day
           << 'T' << std::setw(2) << minute / 60 << ':' << std::setw(2) << minute % 60 << ":00";
    return stream.str();
}
std::map<std::string, double> config() {
    return {{"fast_ema", 2}, {"medium_ema", 3}, {"slow_ema", 5}, {"trend_ema_period", 1},
            {"session_close_minute", 600}, {"opening_window_minutes", 30}, {"min_move_bps", 10}};
}
std::vector<MarketData> history(int direction) {
    std::vector<MarketData> rows;
    double price = direction > 0 ? 100.0 : 200.0;
    const int days[] = {31, 28, 31, 30};
    // Fictional calendar sessions, sufficient full sessions to test period boundaries.
    for (int month = 1; month <= 4; ++month)
        for (int day = 1; day <= days[month - 1]; ++day)
            for (int minute = 570; minute < 600; minute += 2) {
                double next = price + direction * .005;
                rows.push_back(bar(stamp(month, day, minute), price, next)); price = next;
            }
    return rows;
}
void warm(VWAPOpeningStrategy& strategy, const std::vector<MarketData>& rows) {
    require(strategy.initialize(config()), "VWAP initialization");
    for (const auto& row : rows) strategy.generate_signal(row, Position{});
}
TradingSignal opening(VWAPOpeningStrategy& strategy, double price, int direction, int day = 1, double volume = 1000.0) {
    strategy.generate_signal(bar(stamp(5, day, 570), price, price + direction * .01), Position{});
    return strategy.generate_signal(bar(stamp(5, day, 572), price + direction * .01, price + direction * 1.01, volume), Position{});
}
void test_parameters_and_reset() {
    const double nan = std::numeric_limits<double>::quiet_NaN();
    SMACrossoverStrategy sma; EMAStrategy ema; RSIStrategy rsi; VWAPOpeningStrategy vwap;
    require(!sma.initialize({{"short_period", 2.5}, {"long_period", 10}}), "fractional SMA period rejected");
    require(!ema.initialize({{"short_period", nan}, {"long_period", 26}}), "NaN EMA period rejected");
    require(!rsi.initialize({{"period", 2}, {"oversold_threshold", nan}, {"overbought_threshold", 70}}), "NaN RSI threshold rejected");
    require(!vwap.initialize({{"opening_window_minutes", 15.1}}), "fractional opening window rejected");
    require(!vwap.initialize({{"unknown", 1}}), "unknown VWAP parameter rejected");
    require(!vwap.initialize({{"fast_ema", 200}, {"medium_ema", 50}}), "invalid EMA ordering rejected");
    require(!vwap.initialize({{"min_body_fraction", 1.1}}), "body fraction bounds");
    require(!vwap.initialize({{"enable_short_signals", 2}}), "short switch bounds");
    require(!vwap.initialize({{"bar_minutes", 5}, {"exit_buffer_minutes", 2}}), "exit buffer must allow next open");
    require(!vwap.initialize({{"bar_minutes", 4}}), "bar interval must divide configured session duration");
    require(vwap.initialize({}), "VWAP partial map defaults");
    require(vwap.get_parameters().at("slow_ema") == 200, "default slow EMA200");
    require(vwap.get_parameters().at("opening_window_minutes") == 30, "default opening window30");
    auto daily = bar("2025-01-01", 100, 101);
    bool threw = false;
    try { vwap.generate_signal(daily, Position{}); }
    catch (const std::invalid_argument&) { threw = true; }
    require(threw, "daily VWAP data must fail explicitly, not silently return a no-trade result");
    require(vwap.diagnostics().intraday_bars == 0, "daily data does not warm indicators");
    threw = false;
    try { vwap.generate_signal(bar("2025-01-01T09:31:00", 100, 101), Position{}); }
    catch (const std::invalid_argument&) { threw = true; }
    require(threw, "one-minute input cannot silently become two-minute candles");
}
void test_streaming_strategies() {
    SMACrossoverStrategy sma; EMAStrategy ema;
    for (Strategy* strategy : std::vector<Strategy*>{&sma, &ema}) {
        require(strategy->initialize({{"short_period", 2}, {"long_period", 3}}), "crossover initialize");
        for (int i = 0; i < 3; ++i)
            require(strategy->generate_signal(bar(stamp(1, 1, 570 + i * 2), 10, 10), Position{}).type == SignalType::HOLD, "no premature crossover");
        require(strategy->generate_signal(bar(stamp(1, 1, 576), 10, 12), Position{}).type == SignalType::BUY, "bullish crossover");
        Position held; held.quantity = 1;
        require(strategy->generate_signal(bar(stamp(1, 1, 578), 12, 1), held).type == SignalType::SELL, "bearish crossover exits");
        require(strategy->initialize(strategy->get_parameters()), "crossover reset");
        require(strategy->generate_signal(bar(stamp(1, 1, 580), 10, 11), Position{}).type == SignalType::HOLD, "reset discards warmup");
    }
    RSIStrategy rsi;
    require(rsi.initialize({{"period", 2}, {"oversold_threshold", 30}, {"overbought_threshold", 70}}), "RSI initialize");
    for (double price : {10.0, 9.0, 8.0}) require(rsi.generate_signal(bar("2025-01-01", price, price), Position{}).type == SignalType::HOLD, "RSI warmup and no premature recovery");
    require(rsi.generate_signal(bar("2025-01-02", 8, 10), Position{}).type == SignalType::BUY, "Wilder RSI recovery");
    require(rsi.initialize(rsi.get_parameters()), "RSI reset");
    for (int i = 0; i < 10; ++i) require(rsi.generate_signal(bar("2025-01-01", 10, 10), Position{}).type == SignalType::HOLD, "flat RSI neutral");
}
void test_vwap_long_and_reset(const std::vector<MarketData>& rows) {
    VWAPOpeningStrategy strategy;
    warm(strategy, rows);
    const double price = rows.back().close;
    auto signal = opening(strategy, price, 1);
    require(signal.type == SignalType::BUY, "bullish opening must buy: " + signal.reason);
    require(signal.quantity == 0.0, "entry delegates size to risk manager");
    require(signal.valid_until == "2025-05-01T10:00:00", "entry deadline");
    const auto snapshot = strategy.diagnostics();
    require(snapshot.weekly_direction == 1 && snapshot.monthly_direction == 1, "completed trends bullish");
    const double typical0 = (price + .02 + price - .01 + price + .01) / 3.0;
    const double typical1 = (price + 1.02 + price + price + 1.01) / 3.0;
    require(std::abs(snapshot.session_vwap - (typical0 + typical1) / 2.0) < 1e-10, "VWAP typical price times volume");
    require(strategy.generate_signal(bar(stamp(5, 1, 574), price + 1.01, price + 2.01), Position{}).type == SignalType::HOLD, "one entry signal per session");
    auto next = strategy.generate_signal(bar(stamp(5, 2, 570), price + 2.01, price + 2.02), Position{});
    require(next.type == SignalType::HOLD && strategy.diagnostics().session_bars == 1, "session VWAP resets");
    require(strategy.diagnostics().previous_vwap == 0.0, "new session has no old VWAP slope");
    require(strategy.diagnostics().intraday_bars == rows.size() + 4, "EMAs persist across sessions");
    require(strategy.initialize(strategy.get_parameters()), "VWAP reset");
    require(strategy.diagnostics().intraday_bars == 0 && strategy.diagnostics().monthly_completed_bars == 0, "all VWAP state reset");
    auto cold = opening(strategy, price, 1);
    require(cold.type == SignalType::HOLD, "no trade before warmup");
}
void test_vwap_short(const std::vector<MarketData>& rows) {
    VWAPOpeningStrategy strategy;
    warm(strategy, rows);
    const auto signal = opening(strategy, rows.back().close, -1);
    require(signal.type == SignalType::SHORT, "mirror bearish signal must be SHORT: " + signal.reason);
    require(signal.type != SignalType::SELL, "short not a sell of unowned shares");
    require(strategy.diagnostics().weekly_direction == -1 && strategy.diagnostics().monthly_direction == -1, "bearish completed trends");
    auto parameters = config(); parameters["enable_short_signals"] = 0;
    require(strategy.initialize(parameters), "disable short signals");
    for (const auto& row : rows) strategy.generate_signal(row, Position{});
    require(opening(strategy, rows.back().close, -1).type == SignalType::HOLD, "short disabled");
}
void test_vwap_rejections_and_exits(const std::vector<MarketData>& rows) {
    VWAPOpeningStrategy strategy;
    const double price = rows.back().close;
    warm(strategy, rows);
    require(opening(strategy, price, 1, 1, 0).type == SignalType::HOLD, "zero volume cannot buy");
    warm(strategy, rows);
    require(opening(strategy, price, -1).type == SignalType::HOLD, "strong bearish candle cannot trade bullish higher trends");
    warm(strategy, rows);
    strategy.generate_signal(bar(stamp(5, 1, 570), price, price + .01), Position{});
    auto gap = strategy.generate_signal(bar(stamp(5, 1, 576), price + .01, price + 1.01), Position{});
    require(gap.type == SignalType::HOLD && gap.reason.find("Incomplete") != std::string::npos, "missing opening bar blocks entry");
    auto count = strategy.diagnostics().intraday_bars;
    strategy.generate_signal(bar(stamp(5, 1, 576), price + .01, price + 1.01), Position{});
    require(strategy.diagnostics().intraday_bars == count, "duplicate data not counted twice");
    warm(strategy, rows);
    auto params = config(); params["opening_window_minutes"] = 15;
    require(strategy.initialize(params), "15minute choice");
    for (const auto& row : rows) strategy.generate_signal(row, Position{});
    for (int minute = 570; minute < 584; minute += 2) strategy.generate_signal(bar(stamp(5, 1, minute), price, price + .01), Position{});
    auto late = strategy.generate_signal(bar(stamp(5, 1, 584), price, price + 1.0), Position{});
    require(late.type == SignalType::HOLD && late.reason.find("window") != std::string::npos, "signal completion/next fill beyond15min excluded");
    warm(strategy, rows);
    opening(strategy, price, 1);
    Position held; held.quantity = 10;
    auto exit = strategy.generate_signal(bar(stamp(5, 1, 574), price + 1.01, price - 1.0), held);
    require(exit.type == SignalType::SELL && exit.quantity == 10, "VWAP break exits existing long");
    auto timed = strategy.generate_signal(bar(stamp(5, 1, 594), price + 1, price + 2), held);
    require(timed.type == SignalType::SELL && timed.reason.find("session close") != std::string::npos, "time exit");
}
void test_causality_and_repeatability(const std::vector<MarketData>& rows) {
    VWAPOpeningStrategy a, b;
    warm(a, rows); warm(b, rows);
    auto one = opening(a, rows.back().close, 1);
    auto two = opening(b, rows.back().close, 1);
    require(one.type == two.type && one.reason == two.reason && a.diagnostics().session_vwap == b.diagnostics().session_vwap, "same history same result");
    const auto old = a.diagnostics();
    a.generate_signal(bar(stamp(5, 1, 574), rows.back().close, 1), Position{});
    require(a.diagnostics().monthly_direction == old.monthly_direction && a.diagnostics().weekly_direction == old.weekly_direction, "unfinished current week/month cannot change trend");
    require(a.diagnostics().monthly_completed_bars == old.monthly_completed_bars, "only completed monthly bars used");
    VWAPOpeningStrategy cold;
    cold.initialize(config());
    for (const auto& row : rows) {
        if (row.timestamp.substr(0, 7) != "2025-01") break;
        require(cold.generate_signal(row, Position{}).type == SignalType::HOLD, "missing higher-timeframe history holds");
    }
    require(!cold.diagnostics().trend_ready, "no monthly leakage during first month");
}
void test_conflicting_completed_trends(const std::vector<MarketData>& rows) {
    VWAPOpeningStrategy strategy;
    warm(strategy, rows);
    double price = rows.back().close;
    for (int day = 1; day <= 31; ++day) {
        for (int minute = 570; minute < 600; minute += 2) {
            double next = price + (day <= 15 ? .04 : -.01);
            strategy.generate_signal(bar(stamp(5, day, minute), price, next), Position{});
            price = next;
        }
    }
    strategy.generate_signal(bar(stamp(6, 1, 570), price, price - .01), Position{});
    const auto signal = strategy.generate_signal(bar(stamp(6, 1, 572), price - .01, price - 1.01), Position{});
    require(strategy.diagnostics().monthly_direction == 1 && strategy.diagnostics().weekly_direction == -1,
            "fixture creates opposite completed weekly/monthly trends");
    require(signal.type == SignalType::HOLD && signal.reason.find("do not agree") != std::string::npos,
            "opposite higher-timeframe trends cannot trade");
}
void test_truncated_period_close(const std::vector<MarketData>& rows) {
    auto truncated = rows;
    truncated.pop_back(); // The final April session lacks its last configured candle.
    VWAPOpeningStrategy strategy;
    warm(strategy, truncated);
    const auto signal = opening(strategy, truncated.back().close, 1);
    require(signal.type == SignalType::HOLD && !strategy.diagnostics().trend_ready,
            "known truncated monthly close cannot seed a completed trend");
    require(strategy.diagnostics().monthly_completed_bars == 0, "invalid monthly group resets its contiguous warmup");
}
}
int main() {
    try {
        const auto up = history(1), down = history(-1);
        test_parameters_and_reset(); test_streaming_strategies(); test_vwap_long_and_reset(up);
        test_vwap_short(down); test_vwap_rejections_and_exits(up); test_causality_and_repeatability(up);
        test_conflicting_completed_trends(up);
        test_truncated_period_close(up);
        std::cout << "Strategy checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Strategy check failed: " << error.what() << '\n'; return 1;
    }
}
