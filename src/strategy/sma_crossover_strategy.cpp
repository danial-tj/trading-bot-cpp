#include "strategy/strategy.h"
#include "strategy_detail.h"

namespace TradingBot {
SMACrossoverStrategy::SMACrossoverStrategy() : Strategy("SMA_CROSSOVER") { initialize(get_parameters()); }
bool SMACrossoverStrategy::initialize(const std::map<std::string, double>& params) {
    if (!validate_parameters(params)) return false;
    short_period_ = static_cast<int>(params.at("short_period"));
    long_period_ = static_cast<int>(params.at("long_period"));
    parameters_ = params;
    short_prices_.clear(); long_prices_.clear();
    short_sum_ = long_sum_ = previous_difference_ = 0.0;
    previous_ready_ = false;
    return true;
}
TradingSignal SMACrossoverStrategy::generate_signal(const MarketData& data, const Position& position) {
    if (!StrategyDetail::valid_close(data)) return StrategyDetail::hold(data, "Invalid close");
    short_prices_.push_back(data.close); short_sum_ += data.close;
    long_prices_.push_back(data.close); long_sum_ += data.close;
    if (short_prices_.size() > static_cast<std::size_t>(short_period_)) {
        short_sum_ -= short_prices_.front(); short_prices_.pop_front();
    }
    if (long_prices_.size() > static_cast<std::size_t>(long_period_)) {
        long_sum_ -= long_prices_.front(); long_prices_.pop_front();
    }
    if (long_prices_.size() < static_cast<std::size_t>(long_period_))
        return StrategyDetail::hold(data, "SMA warmup");
    double difference = short_sum_ / short_period_ - long_sum_ / long_period_;
    auto signal = previous_ready_ ? StrategyDetail::crossover(data, position, previous_difference_, difference, "SMA")
                                  : StrategyDetail::hold(data, "SMA crossover warmup");
    previous_difference_ = difference; previous_ready_ = true;
    return signal;
}
std::map<std::string, double> SMACrossoverStrategy::get_parameters() const {
    return {{"short_period", short_period_}, {"long_period", long_period_}};
}
bool SMACrossoverStrategy::validate_parameters(const std::map<std::string, double>& params) const {
    return StrategyDetail::crossover_parameters(params);
}
} // namespace TradingBot
