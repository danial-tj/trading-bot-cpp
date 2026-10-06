#include "strategy/strategy.h"
#include "strategy_detail.h"
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace TradingBot {
namespace {
std::map<std::string, double> defaults() {
    return {{"bar_minutes", 2}, {"session_open_minute", 570}, {"session_close_minute", 960},
            {"opening_window_minutes", 30}, {"fast_ema", 20}, {"medium_ema", 50}, {"slow_ema", 200},
            {"min_body_fraction", .6}, {"min_close_location", .75}, {"min_move_bps", 10},
            {"min_vwap_slope_bps", 0}, {"min_vwap_distance_bps", 0}, {"trend_ema_period", 2},
            {"exit_buffer_minutes", 4}, {"enable_short_signals", 1}};
}
struct ParsedTime { std::string date, canonical; int year = 0, month = 0, day = 0, minute = 0; long long days = 0; };
// Gregorian civil date to days since 1970-01-01; timezone conversion is intentionally absent.
long long civil_days(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    const int era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097LL + doe - 719468;
}
bool parse_time(const std::string& timestamp, ParsedTime& result) {
    if (timestamp.size() != 16 && timestamp.size() != 19) return false;
    if (timestamp[4] != '-' || timestamp[7] != '-' || (timestamp[10] != 'T' && timestamp[10] != ' ')
        || timestamp[13] != ':' || (timestamp.size() == 19 && timestamp.substr(16) != ":00")) return false;
    for (std::size_t i = 0; i < 16; ++i) {
        if (i == 4 || i == 7 || i == 10 || i == 13) continue;
        if (timestamp[i] < '0' || timestamp[i] > '9') return false;
    }
    result.year = std::stoi(timestamp.substr(0, 4)); result.month = std::stoi(timestamp.substr(5, 2));
    result.day = std::stoi(timestamp.substr(8, 2));
    int hour = std::stoi(timestamp.substr(11, 2)), minute = std::stoi(timestamp.substr(14, 2));
    if (result.year < 1900 || result.year > 9999 || result.month < 1 || result.month > 12 || hour > 23 || minute > 59) return false;
    const int month_days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    const bool leap = result.year % 4 == 0 && (result.year % 100 != 0 || result.year % 400 == 0);
    if (result.day < 1 || result.day > month_days[result.month - 1] + (result.month == 2 && leap ? 1 : 0)) return false;
    result.minute = hour * 60 + minute; result.date = timestamp.substr(0, 10);
    result.canonical = result.date + "T" + timestamp.substr(11, 5) + ":00";
    result.days = civil_days(result.year, result.month, result.day);
    return true;
}
std::string at_minute(const std::string& date, int minute) {
    std::ostringstream stream;
    stream << date << 'T' << std::setfill('0') << std::setw(2) << minute / 60 << ':' << std::setw(2) << minute % 60 << ":00";
    return stream.str();
}
bool valid_bar(const MarketData& bar) {
    return StrategyDetail::valid_close(bar) && std::isfinite(bar.open) && std::isfinite(bar.high)
        && std::isfinite(bar.low) && std::isfinite(bar.volume) && bar.open > 0.0 && bar.low > 0.0
        && bar.volume >= 0.0 && bar.high >= std::max(bar.open, bar.close) && bar.low <= std::min(bar.open, bar.close);
}
} // namespace

VWAPOpeningStrategy::VWAPOpeningStrategy() : Strategy("VWAP_OPENING") { initialize(defaults()); }
bool VWAPOpeningStrategy::validate_parameters(const std::map<std::string, double>& params) const {
    auto config = defaults();
    for (const auto& item : params) {
        if (config.find(item.first) == config.end() || !std::isfinite(item.second)) return false;
        config[item.first] = item.second;
    }
    auto integer = [&](const char* key, double min, double max) { return StrategyDetail::integer(config.at(key), min, max); };
    if (!integer("bar_minutes", 1, 30) || !integer("session_open_minute", 0, 1438)
        || !integer("session_close_minute", 1, 1439) || !integer("opening_window_minutes", 1, 120)
        || !integer("fast_ema", 1, 100000) || !integer("medium_ema", 2, 100000) || !integer("slow_ema", 3, 100000)
        || !integer("trend_ema_period", 1, 120) || !integer("exit_buffer_minutes", 1, 1438)
        || !integer("enable_short_signals", 0, 1)) return false;
    const double duration = config.at("session_close_minute") - config.at("session_open_minute");
    if (duration <= 0 || config.at("opening_window_minutes") > duration
        || std::fmod(duration, config.at("bar_minutes")) != 0.0
        || config.at("bar_minutes") > config.at("opening_window_minutes")
        || config.at("exit_buffer_minutes") < config.at("bar_minutes") || config.at("exit_buffer_minutes") >= duration
        || config.at("fast_ema") >= config.at("medium_ema") || config.at("medium_ema") >= config.at("slow_ema")) return false;
    for (const auto* key : {"min_body_fraction", "min_close_location"})
        if (config.at(key) < 0.0 || config.at(key) > 1.0) return false;
    for (const auto* key : {"min_move_bps", "min_vwap_slope_bps", "min_vwap_distance_bps"})
        if (config.at(key) < 0.0 || config.at(key) > 10000.0) return false;
    return true;
}
bool VWAPOpeningStrategy::initialize(const std::map<std::string, double>& params) {
    if (!validate_parameters(params)) return false;
    parameters_ = defaults();
    for (const auto& item : params) parameters_[item.first] = item.second;
    fast_.reset(static_cast<int>(parameters_.at("fast_ema")));
    medium_.reset(static_cast<int>(parameters_.at("medium_ema")));
    slow_.reset(static_cast<int>(parameters_.at("slow_ema")));
    weekly_.reset(static_cast<int>(parameters_.at("trend_ema_period")));
    monthly_.reset(static_cast<int>(parameters_.at("trend_ema_period")));
    diagnostics_ = VWAPDiagnostics{}; last_timestamp_.clear();
    cumulative_pv_ = cumulative_volume_ = 0.0L;
    entry_attempted_ = session_complete_ = false; last_session_minute_ = -1;
    return true;
}
std::map<std::string, double> VWAPOpeningStrategy::get_parameters() const { return parameters_; }
void VWAPOpeningStrategy::CompletedTrend::reset(int period) {
    *this = CompletedTrend{}; ema.reset(period);
}
void VWAPOpeningStrategy::CompletedTrend::push(long long key, double close) {
    if (!have_group) { current_key = key; have_group = true; latest_close = close; return; }
    if (key != current_key) {
        // The initial group may be partial because the input can begin midweek/month.
        if (first_group) first_group = false;
        else if (current_group_complete) {
            const bool previously_ready = ema.ready();
            const double previous_ema = ema.value;
            ema.push(latest_close); ++completed;
            ready = previously_ready && ema.ready();
            direction = ready && (ema.period == 1 || latest_close > ema.value) && ema.value > previous_ema ? 1
                      : ready && (ema.period == 1 || latest_close < ema.value) && ema.value < previous_ema ? -1 : 0;
        } else {
            // A known missing opening/interior/closing bar cannot be a complete period.
            ema.reset(ema.period);
            completed = 0; ready = false; direction = 0;
        }
        current_key = key;
        current_group_complete = true;
    }
    latest_close = close;
}
TradingSignal VWAPOpeningStrategy::generate_signal(const MarketData& data, const Position& position) {
    auto signal = StrategyDetail::hold(data, "");
    auto finish = [&](const std::string& reason) { signal.reason = diagnostics_.reason = reason; return signal; };
    ParsedTime time;
    if (!valid_bar(data)) return finish("Invalid OHLCV bar");
    if (!parse_time(data.timestamp, time))
        throw std::invalid_argument("VWAP requires exchange-local intraday bar-start timestamps at whole minutes; daily data is unsupported");
    if (!last_timestamp_.empty() && time.canonical <= last_timestamp_) return finish("Duplicate or out-of-order timestamp ignored");
    const int open = static_cast<int>(parameters_.at("session_open_minute"));
    const int close = static_cast<int>(parameters_.at("session_close_minute"));
    const int bar_minutes = static_cast<int>(parameters_.at("bar_minutes"));
    const int completion = time.minute + bar_minutes;
    if (time.minute < open || completion > close) return finish("Outside configured regular session");
    if ((time.minute - open) % bar_minutes != 0)
        throw std::invalid_argument("Intraday bar starts do not match configured bar_minutes; provide correctly aggregated bars");
    last_timestamp_ = time.canonical;
    if (diagnostics_.session_date != time.date) {
        if (!diagnostics_.session_date.empty() && (!session_complete_ || last_session_minute_ + bar_minutes != close)) {
            weekly_.current_group_complete = false;
            monthly_.current_group_complete = false;
        }
        diagnostics_.session_date = time.date;
        diagnostics_.session_vwap = diagnostics_.previous_vwap = 0.0;
        diagnostics_.session_bars = 0;
        cumulative_pv_ = cumulative_volume_ = 0.0L;
        entry_attempted_ = false; session_complete_ = time.minute == open; last_session_minute_ = -1;
    }
    if (last_session_minute_ >= 0 && time.minute != last_session_minute_ + bar_minutes) session_complete_ = false;
    last_session_minute_ = time.minute;
    diagnostics_.previous_vwap = diagnostics_.session_vwap;
    if (data.volume > 0.0) {
        const long double typical = (static_cast<long double>(data.high) + data.low + data.close) / 3.0L;
        cumulative_pv_ += typical * data.volume; cumulative_volume_ += data.volume;
        diagnostics_.session_vwap = static_cast<double>(cumulative_pv_ / cumulative_volume_);
    }
    ++diagnostics_.session_bars; ++diagnostics_.intraday_bars;
    fast_.push(data.close); medium_.push(data.close); slow_.push(data.close);
    diagnostics_.fast_ema = fast_.value; diagnostics_.medium_ema = medium_.value; diagnostics_.slow_ema = slow_.value;
    // Monday-based week identity, including year boundaries. 1970-01-01 was Thursday.
    const long long shifted = time.days + 3;
    const long long week_key = shifted >= 0 ? shifted / 7 : (shifted - 6) / 7;
    weekly_.push(week_key, data.close); monthly_.push(time.year * 12LL + time.month, data.close);
    diagnostics_.weekly_direction = weekly_.direction; diagnostics_.monthly_direction = monthly_.direction;
    diagnostics_.weekly_completed_bars = weekly_.completed; diagnostics_.monthly_completed_bars = monthly_.completed;
    diagnostics_.intraday_ready = slow_.ready(); diagnostics_.trend_ready = weekly_.ready && monthly_.ready;
    const bool near_close = completion >= close - parameters_.at("exit_buffer_minutes");
    if (position.quantity > 0.0 && (near_close || (diagnostics_.session_vwap > 0.0 && data.close < diagnostics_.session_vwap))) {
        signal.type = SignalType::SELL; signal.quantity = position.quantity;
        return finish(near_close ? "Exit long before configured session close" : "Long close crossed below session VWAP");
    }
    if (position.quantity < 0.0 && (near_close || (diagnostics_.session_vwap > 0.0 && data.close > diagnostics_.session_vwap))) {
        signal.type = SignalType::COVER; signal.quantity = -position.quantity;
        return finish(near_close ? "Cover short before configured session close" : "Short close crossed above session VWAP");
    }
    if (position.quantity != 0.0) return finish("Position already open");
    if (entry_attempted_) return finish("Opening entry already signaled this session");
    if (near_close) return finish("No opening entry near session close");
    const int deadline = open + static_cast<int>(parameters_.at("opening_window_minutes"));
    // Strict bound leaves time for the next-open fill inside the opening window.
    if (completion >= deadline) return finish("Outside opening entry window (including next-open execution)");
    if (!session_complete_) return finish("Incomplete opening-session bars; entry disabled for this session");
    if (!diagnostics_.intraday_ready) return finish("Intraday EMA warmup: need slow_ema regular-session bars");
    if (!diagnostics_.trend_ready) return finish("Higher-timeframe warmup: need trend_ema_period + 1 completed weeks and months after initial partial groups");
    if (weekly_.direction == 0 || weekly_.direction != monthly_.direction) return finish("Completed weekly and monthly trends do not agree");
    if (data.volume <= 0.0) return finish("Zero-volume candle cannot trigger an entry");
    if (diagnostics_.previous_vwap <= 0.0) return finish("Need prior positive-volume session VWAP to measure direction");
    const double range = data.high - data.low;
    const double body = data.close - data.open;
    if (range <= 0.0 || std::abs(body) / range < parameters_.at("min_body_fraction")
        || std::abs(body) / data.open * 10000.0 < parameters_.at("min_move_bps")) return finish("Candle body is not strong enough");
    const double location = (data.close - data.low) / range;
    const double slope_bps = (diagnostics_.session_vwap / diagnostics_.previous_vwap - 1.0) * 10000.0;
    const double distance_bps = (data.close / diagnostics_.session_vwap - 1.0) * 10000.0;
    const int direction = weekly_.direction;
    const bool strong = direction == 1 ? body > 0.0 && location >= parameters_.at("min_close_location")
                                      : body < 0.0 && location <= 1.0 - parameters_.at("min_close_location");
    const bool vwap_aligned = direction * slope_bps > 0.0 && direction * slope_bps >= parameters_.at("min_vwap_slope_bps")
        && direction * distance_bps > 0.0 && direction * distance_bps >= parameters_.at("min_vwap_distance_bps");
    const bool ema_aligned = direction == 1 ? data.close > fast_.value && fast_.value > medium_.value && medium_.value > slow_.value
                                          : data.close < fast_.value && fast_.value < medium_.value && medium_.value < slow_.value;
    if (!strong) return finish("Candle direction or close location does not match higher-timeframe trend");
    if (!vwap_aligned) return finish("Price or VWAP slope does not match higher-timeframe trend");
    if (!ema_aligned) return finish("Intraday EMA stack does not match higher-timeframe trend");
    if (direction == -1 && parameters_.at("enable_short_signals") == 0) return finish("Bearish opening setup; short signals disabled");
    signal.type = direction == 1 ? SignalType::BUY : SignalType::SHORT;
    signal.quantity = 0.0; // Automatic whole-share risk sizing at the execution open.
    signal.valid_until = at_minute(time.date, deadline);
    entry_attempted_ = true;
    return finish(direction == 1 ? "Strong bullish opening candle, rising VWAP, bullish EMA stack and completed weekly/monthly trends"
                                 : "Strong bearish opening candle, falling VWAP, bearish EMA stack and completed weekly/monthly trends (short signal)");
}
} // namespace TradingBot
