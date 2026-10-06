#pragma once
#include "strategy/strategy.h"
#include <cmath>

namespace TradingBot::StrategyDetail {
inline bool integer(double value, double minimum, double maximum) {
    return std::isfinite(value) && value >= minimum && value <= maximum && std::floor(value) == value;
}
inline bool crossover_parameters(const std::map<std::string, double>& params) {
    const auto short_it = params.find("short_period"), long_it = params.find("long_period");
    return params.size() == 2 && short_it != params.end() && long_it != params.end()
        && integer(short_it->second, 1, 100000) && integer(long_it->second, 2, 100000)
        && short_it->second < long_it->second;
}
inline TradingSignal hold(const MarketData& data, const std::string& reason) {
    TradingSignal signal;
    signal.price = data.close;
    signal.timestamp = data.timestamp;
    signal.reason = reason;
    return signal;
}
inline bool valid_close(const MarketData& data) { return std::isfinite(data.close) && data.close > 0; }
inline TradingSignal crossover(const MarketData& data, const Position& position,
                               double previous, double current, const std::string& name) {
    auto signal = hold(data, "No " + name + " crossover");
    if (previous <= 0.0 && current > 0.0 && position.quantity == 0.0) {
        signal.type = SignalType::BUY;
        signal.quantity = 0.0; // Let the risk manager size the entry from available capacity.
        signal.reason = "Short " + name + " crossed above long " + name;
    } else if (previous >= 0.0 && current < 0.0 && position.quantity > 0.0) {
        signal.type = SignalType::SELL;
        signal.quantity = position.quantity;
        signal.reason = "Short " + name + " crossed below long " + name;
    }
    return signal;
}
} // namespace TradingBot::StrategyDetail
