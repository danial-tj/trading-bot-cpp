#include "risk/risk_manager.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace TradingBot {
bool RiskManager::initialize(const RiskParameters& params) {
    auto fraction = [](double value, bool zero) { return std::isfinite(value) && value <= 1 && (zero ? value >= 0 : value > 0); };
    if (!fraction(params.max_position_size, false) || !fraction(params.max_drawdown, false) ||
        !fraction(params.stop_loss_pct, true) || !fraction(params.take_profit_pct, true) ||
        !fraction(params.max_daily_loss, false) || !std::isfinite(params.position_sizing_atr) || params.position_sizing_atr <= 0) return false;
    risk_params_ = params;
    return true;
}
std::string RiskManager::rejection_reason(const TradingSignal& signal, const PortfolioState& portfolio) const {
    if (signal.type == SignalType::HOLD) return "hold is not an order";
    if (!std::isfinite(signal.price) || signal.price <= 0) return "order price must be finite and positive";
    // Risk limits must never prevent reduction of existing long exposure.
    if (signal.type == SignalType::SELL) return portfolio.quantity > 0 ? "" : "no long position to sell";
    if (signal.type != SignalType::BUY) return "short execution is disabled";
    if (portfolio.current_drawdown >= risk_params_.max_drawdown) return "maximum drawdown limit reached";
    if (portfolio.daily_loss_locked || portfolio.daily_loss >= risk_params_.max_daily_loss) return "daily loss limit reached";
    return {};
}
bool RiskManager::validate_trade(const TradingSignal& signal, const PortfolioState& portfolio) {
    return rejection_reason(signal, portfolio).empty();
}
double RiskManager::calculate_position_size(const TradingSignal& signal, const PortfolioState& portfolio, const MarketData&) {
    if (!std::isfinite(signal.price) || signal.price <= 0) return 0;
    if (signal.type == SignalType::SELL) return portfolio.quantity;
    if (signal.type != SignalType::BUY) return 0;
    const auto remaining = std::max(0.0, portfolio.total_value * risk_params_.max_position_size - portfolio.quantity * signal.price);
    return std::floor(std::min(portfolio.cash, remaining) / signal.price);
}
void RiskManager::update_portfolio_state(PortfolioState&, const TradingSignal&, const MarketData&) {
    throw std::logic_error("apply executed fills through Ledger; RiskManager does not own accounting");
}
std::string RiskManager::closure_reason(const Position& position, const MarketData& data, const PortfolioState& portfolio) const {
    if (position.quantity <= 0 || position.avg_price <= 0) return {};
    if (portfolio.daily_loss_locked || portfolio.daily_loss >= risk_params_.max_daily_loss) return "daily loss limit: close at next available open";
    if (portfolio.current_drawdown >= risk_params_.max_drawdown) return "maximum drawdown: close at next available open";
    const auto change = (data.close - position.avg_price) / position.avg_price;
    if (risk_params_.stop_loss_pct > 0 && change <= -risk_params_.stop_loss_pct) return "close-based stop loss: next available open";
    if (risk_params_.take_profit_pct > 0 && change >= risk_params_.take_profit_pct) return "close-based take profit: next available open";
    return {};
}
bool RiskManager::should_close_position(const Position& position, const MarketData& data, const PortfolioState& portfolio) {
    return !closure_reason(position, data, portfolio).empty();
}
const RiskParameters& RiskManager::get_risk_parameters() const { return risk_params_; }
void RiskManager::set_risk_parameters(const RiskParameters& params) {
    if (!initialize(params)) throw std::invalid_argument("invalid risk parameters");
}
double RiskManager::calculate_atr(const std::vector<MarketData>& data, int period) {
    if (period <= 0 || data.size() <= static_cast<size_t>(period)) throw std::invalid_argument("ATR requires a positive period and period+1 bars");
    double sum = 0;
    for (size_t i = data.size() - static_cast<size_t>(period); i < data.size(); ++i)
        sum += std::max({data[i].high - data[i].low, std::abs(data[i].high - data[i-1].close), std::abs(data[i].low - data[i-1].close)});
    return sum / period;
}
double RiskManager::calculate_drawdown(double peak, double current) {
    if (!std::isfinite(peak) || !std::isfinite(current)) throw std::invalid_argument("drawdown values must be finite");
    return peak > 0 ? std::max(0.0, (peak - current) / peak) : 0;
}
}
