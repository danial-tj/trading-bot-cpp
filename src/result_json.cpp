#include "configuration.h"
#include <cmath>
namespace TradingBot {
Json results_to_json(const BacktestResults& r) {
    auto optional_number = [](double x) -> Json { return std::isfinite(x) ? Json(x) : Json(nullptr); };
    Json out = {{"total_return",r.total_return}, {"annualized_return",optional_number(r.annualized_return)},
        {"sharpe_ratio",optional_number(r.sharpe_ratio)}, {"max_drawdown",r.max_drawdown},
        {"win_rate",r.closed_trades ? Json(r.win_rate) : Json(nullptr)}, {"total_trades",r.total_trades},
        {"closed_trades",r.closed_trades}, {"winning_trades",r.winning_trades}, {"losing_trades",r.losing_trades},
        {"avg_win",r.winning_trades ? Json(r.avg_win) : Json(nullptr)},
        {"avg_loss",r.losing_trades ? Json(r.avg_loss) : Json(nullptr)}, {"profit_factor",optional_number(r.profit_factor)},
        {"final_cash",r.final_cash}, {"final_equity",r.final_equity}, {"final_quantity",static_cast<std::int64_t>(r.final_quantity)},
        {"cost_basis",r.cost_basis}, {"realized_pnl",r.realized_pnl}, {"unrealized_pnl",r.unrealized_pnl},
        {"total_fees",r.total_fees}, {"total_slippage",r.total_slippage},
        {"initial_cash_cents",r.initial_cash_cents}, {"final_cash_cents",r.final_cash_cents},
        {"final_equity_cents",r.final_equity_cents}, {"cost_basis_cents",r.cost_basis_cents},
        {"realized_pnl_cents",r.realized_pnl_cents}, {"unrealized_pnl_cents",r.unrealized_pnl_cents},
        {"total_fees_cents",r.total_fees_cents}, {"equity_curve",r.equity_curve}, {"equity_timestamps",r.equity_timestamps},
        {"trades",Json::array()}, {"rejections",Json::array()}, {"events",Json::array()},
        {"strategy_diagnostics",nullptr}};
    if (r.strategy_diagnostics.available) {
        const auto& d = r.strategy_diagnostics;
        const auto& last = d.last_diagnostics;
        auto timestamp = [](const std::string& value) -> Json { return value.empty() ? Json(nullptr) : Json(value); };
        out["strategy_diagnostics"] = {
            {"strategy","VWAP_OPENING"}, {"scope","evaluation bars; prior-date warmup is counted separately"},
            {"readiness_definition","Enough indicator history only. Matching trend directions, candle filters, existing exposure, prior entry attempts and risk approval are separate conditions."},
            {"timestamp_convention","exchange-local bar starts; diagnostics observed after that bar completes"},
            {"observed_bars",d.observed_bars}, {"evaluated_bars",d.evaluated_bars}, {"warmup_bars",d.warmup_bars},
            {"opening_bars",d.opening_bars}, {"intraday_ready_opening_bars",d.intraday_ready_opening_bars},
            {"trend_ready_opening_bars",d.trend_ready_opening_bars}, {"ready_opening_bars",d.ready_opening_bars},
            {"long_signals",d.long_signals}, {"short_signals",d.short_signals},
            {"bars_with_open_position",d.bars_with_open_position},
            {"hold_reasons",d.hold_reasons}, {"opening_hold_reasons",d.opening_hold_reasons},
            {"first_evaluated_timestamp",timestamp(d.first_evaluated_timestamp)},
            {"last_evaluated_timestamp",timestamp(d.last_evaluated_timestamp)},
            {"first_ready_timestamp",timestamp(d.first_ready_timestamp)},
            {"last_regular_timestamp",timestamp(d.last_regular_timestamp)},
            {"last_bar_regular_session",d.last_bar_regular_session},
            {"last_diagnostics", {
                {"session_date",timestamp(last.session_date)}, {"reason",last.reason},
                {"session_vwap",optional_number(last.session_vwap)}, {"previous_vwap",optional_number(last.previous_vwap)},
                {"fast_ema",optional_number(last.fast_ema)}, {"medium_ema",optional_number(last.medium_ema)},
                {"slow_ema",optional_number(last.slow_ema)}, {"weekly_direction",last.weekly_direction},
                {"monthly_direction",last.monthly_direction}, {"weekly_completed_bars",last.weekly_completed_bars},
                {"monthly_completed_bars",last.monthly_completed_bars}, {"intraday_bars",last.intraday_bars},
                {"session_bars",last.session_bars}, {"intraday_ready",last.intraday_ready}, {"trend_ready",last.trend_ready}
            }}
        };
    }
    for (const auto& t : r.trades) out["trades"].push_back({{"timestamp",t.timestamp}, {"signal_timestamp",t.signal_timestamp},
        {"action",t.action}, {"reason",t.reason}, {"price",t.price}, {"quantity",t.quantity}, {"commission",t.commission},
        {"pnl",t.pnl}, {"slippage",t.slippage}, {"cash_after",t.cash_after}, {"position_after",t.position_after}});
    for (const auto& v : r.rejections) out["rejections"].push_back({{"timestamp",v.timestamp}, {"action",v.action},
        {"signal_timestamp",v.signal_timestamp}, {"reason",v.reason}});
    for (const auto& e : r.events) out["events"].push_back({{"event_id",e.event_id}, {"timestamp",e.timestamp}, {"type",e.type},
        {"quantity",e.quantity}, {"quantity_after",e.quantity_after}, {"price_cents",e.price_cents}, {"fee_cents",e.fee_cents},
        {"cash_delta_cents",e.cash_delta_cents}, {"cash_after_cents",e.cash_after_cents},
        {"cost_basis_after_cents",e.cost_basis_after_cents}, {"realized_pnl_after_cents",e.realized_pnl_after_cents},
        {"price",e.price}, {"fee",e.fee}, {"cash_delta",e.cash_delta}, {"cash_after",e.cash_after},
        {"cost_basis_after",e.cost_basis_after}, {"realized_pnl_after",e.realized_pnl_after}});
    return out;
}
}
