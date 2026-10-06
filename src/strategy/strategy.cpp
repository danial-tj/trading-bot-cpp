#include "strategy/strategy.h"
#include "strategy_detail.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace TradingBot {
Strategy::Strategy(const std::string& name) : name_(name) {}
Strategy::~Strategy() = default;
void Strategy::update(const MarketData&) {}
const std::string& Strategy::get_name() const { return name_; }

void StreamingEMA::reset(int new_period) {
    period = new_period;
    count = 0;
    value = seed_sum = 0.0;
}
void StreamingEMA::push(double close) {
    ++count;
    if (count <= static_cast<std::size_t>(period)) {
        seed_sum += close;
        if (ready()) value = seed_sum / period;
    } else {
        value += (2.0 / (period + 1.0)) * (close - value);
    }
}
bool StreamingEMA::ready() const { return count >= static_cast<std::size_t>(period); }

double Strategy::calculate_sma(const std::vector<MarketData>& data, int period) {
    if (period < 1 || data.size() < static_cast<std::size_t>(period))
        throw std::invalid_argument("Insufficient SMA history or invalid period");
    double sum = 0.0;
    for (auto i = data.size() - period; i < data.size(); ++i) sum += data[i].close;
    return sum / period;
}
double Strategy::calculate_ema(const std::vector<MarketData>& data, int period) {
    if (period < 1 || data.size() < static_cast<std::size_t>(period))
        throw std::invalid_argument("Insufficient EMA history or invalid period");
    StreamingEMA ema;
    ema.reset(period);
    for (const auto& bar : data) ema.push(bar.close);
    return ema.value;
}
double Strategy::calculate_rsi(const std::vector<MarketData>& data, int period) {
    if (period < 1 || data.size() < static_cast<std::size_t>(period) + 1)
        throw std::invalid_argument("Insufficient RSI history or invalid period");
    double gain = 0.0, loss = 0.0;
    for (std::size_t i = 1; i < data.size(); ++i) {
        double change = data[i].close - data[i - 1].close;
        double up = std::max(change, 0.0), down = std::max(-change, 0.0);
        if (i <= static_cast<std::size_t>(period)) {
            gain += up / period; loss += down / period;
        } else {
            gain = (gain * (period - 1) + up) / period;
            loss = (loss * (period - 1) + down) / period;
        }
    }
    if (gain == 0.0 && loss == 0.0) return 50.0;
    if (loss == 0.0) return 100.0;
    return 100.0 - 100.0 / (1.0 + gain / loss);
}
} // namespace TradingBot
