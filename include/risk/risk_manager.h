#pragma once
#include "strategy/strategy.h"
#include <string>
#include <vector>

namespace TradingBot {
struct RiskParameters {
    double max_position_size = 0.02;
    double max_drawdown = 0.20;
    double stop_loss_pct = 0.05;
    double take_profit_pct = 0.10;
    double max_daily_loss = 0.05;
    double position_sizing_atr = 2.0; // Legacy field; ATR-based sizing is unsupported.
};
struct PortfolioState {
    double cash = 0, total_value = 0, unrealized_pnl = 0, realized_pnl = 0;
    double max_drawdown = 0, current_drawdown = 0;
    double quantity = 0, cost_basis = 0, daily_start_value = 0, daily_loss = 0;
    bool daily_loss_locked = false;
};
class RiskManager {
public:
    bool initialize(const RiskParameters& params);
    bool validate_trade(const TradingSignal& signal, const PortfolioState& portfolio);
    std::string rejection_reason(const TradingSignal& signal, const PortfolioState& portfolio) const;
    double calculate_position_size(const TradingSignal& signal, const PortfolioState& portfolio, const MarketData& data);
    // Accounting belongs exclusively to Ledger. Kept only to fail legacy calls explicitly.
    void update_portfolio_state(PortfolioState&, const TradingSignal&, const MarketData&);
    bool should_close_position(const Position& position, const MarketData& data, const PortfolioState& portfolio);
    std::string closure_reason(const Position& position, const MarketData& data, const PortfolioState& portfolio) const;
    const RiskParameters& get_risk_parameters() const;
    void set_risk_parameters(const RiskParameters& params);
    double calculate_atr(const std::vector<MarketData>& data, int period);
    double calculate_drawdown(double peak, double current);
private:
    RiskParameters risk_params_;
};
}
