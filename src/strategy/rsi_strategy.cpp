#include "strategy/strategy.h"
#include "strategy_detail.h"
#include <algorithm>
#include <cmath>

namespace TradingBot {
RSIStrategy::RSIStrategy() : Strategy("RSI_STRATEGY") { initialize(get_parameters()); }
bool RSIStrategy::initialize(const std::map<std::string, double>& params) {
    if (!validate_parameters(params)) return false;
    rsi_period_ = static_cast<int>(params.at("period"));
    oversold_threshold_ = params.at("oversold_threshold");
    overbought_threshold_ = params.at("overbought_threshold");
    parameters_ = params;
    changes_ = 0;
    previous_close_ = average_gain_ = average_loss_ = 0.0;
    previous_rsi_ = 50.0;
    have_close_ = previous_ready_ = false;
    return true;
}
TradingSignal RSIStrategy::generate_signal(const MarketData& data, const Position& position) {
    if (!StrategyDetail::valid_close(data)) return StrategyDetail::hold(data, "Invalid close");
    auto signal = StrategyDetail::hold(data, "RSI warmup");
    if (!have_close_) { previous_close_ = data.close; have_close_ = true; return signal; }
    const double change = data.close - previous_close_;
    previous_close_ = data.close;
    const double up = std::max(change, 0.0), down = std::max(-change, 0.0);
    ++changes_;
    if (changes_ <= static_cast<std::size_t>(rsi_period_)) {
        average_gain_ += up / rsi_period_; average_loss_ += down / rsi_period_;
    } else {
        average_gain_ = (average_gain_ * (rsi_period_ - 1) + up) / rsi_period_;
        average_loss_ = (average_loss_ * (rsi_period_ - 1) + down) / rsi_period_;
    }
    if (changes_ < static_cast<std::size_t>(rsi_period_)) return signal;
    double rsi = average_gain_ == 0.0 && average_loss_ == 0.0 ? 50.0
               : average_loss_ == 0.0 ? 100.0 : 100.0 - 100.0 / (1.0 + average_gain_ / average_loss_);
    signal.reason = "No RSI threshold recovery";
    if (previous_ready_ && previous_rsi_ <= oversold_threshold_ && rsi > oversold_threshold_ && position.quantity == 0.0) {
        signal.type = SignalType::BUY; signal.quantity = 0.0;
        signal.reason = "RSI recovered above oversold threshold";
    } else if (previous_ready_ && previous_rsi_ >= overbought_threshold_ && rsi < overbought_threshold_ && position.quantity > 0.0) {
        signal.type = SignalType::SELL; signal.quantity = position.quantity;
        signal.reason = "RSI crossed below overbought threshold";
    }
    previous_rsi_ = rsi; previous_ready_ = true;
    return signal;
}
std::map<std::string, double> RSIStrategy::get_parameters() const {
    return {{"period", rsi_period_}, {"oversold_threshold", oversold_threshold_}, {"overbought_threshold", overbought_threshold_}};
}
bool RSIStrategy::validate_parameters(const std::map<std::string, double>& params) const {
    const auto p = params.find("period"), low = params.find("oversold_threshold"), high = params.find("overbought_threshold");
    return params.size() == 3 && p != params.end() && low != params.end() && high != params.end()
        && StrategyDetail::integer(p->second, 2, 100000)
        && std::isfinite(low->second) && std::isfinite(high->second)
        && low->second >= 0.0 && low->second < 50.0 && high->second > 50.0 && high->second <= 100.0;
}
} // namespace TradingBot
