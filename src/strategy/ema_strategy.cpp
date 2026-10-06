#include "strategy/strategy.h"
#include "strategy_detail.h"

namespace TradingBot {
EMAStrategy::EMAStrategy() : Strategy("EMA_STRATEGY") { initialize({{"short_period", 12}, {"long_period", 26}}); }
bool EMAStrategy::initialize(const std::map<std::string, double>& params) {
    if (!validate_parameters(params)) return false;
    short_ema_.reset(static_cast<int>(params.at("short_period")));
    long_ema_.reset(static_cast<int>(params.at("long_period")));
    parameters_ = params;
    previous_difference_ = 0.0; previous_ready_ = false;
    return true;
}
TradingSignal EMAStrategy::generate_signal(const MarketData& data, const Position& position) {
    if (!StrategyDetail::valid_close(data)) return StrategyDetail::hold(data, "Invalid close");
    short_ema_.push(data.close); long_ema_.push(data.close);
    if (!long_ema_.ready()) return StrategyDetail::hold(data, "EMA warmup");
    double difference = short_ema_.value - long_ema_.value;
    auto signal = previous_ready_ ? StrategyDetail::crossover(data, position, previous_difference_, difference, "EMA")
                                  : StrategyDetail::hold(data, "EMA crossover warmup");
    previous_difference_ = difference; previous_ready_ = true;
    return signal;
}
std::map<std::string, double> EMAStrategy::get_parameters() const {
    return {{"short_period", short_ema_.period}, {"long_period", long_ema_.period}};
}
bool EMAStrategy::validate_parameters(const std::map<std::string, double>& params) const {
    return StrategyDetail::crossover_parameters(params);
}
} // namespace TradingBot
